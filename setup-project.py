#!/usr/bin/env python3
"""
setup-project.py — Integrate session-knowledge + tentacle orchestration into a project.

Installs skills, instructions (enforcement), and optionally patches CLAUDE.md.
Single entry point for setting up all AI knowledge tools in any project.

Usage:
    python setup-project.py                        # Auto-detect project root (git root)
    python setup-project.py /path/to/project       # Explicit project root
    python setup-project.py --skill-only            # Only install skills, skip patching
    python setup-project.py --no-tentacle           # Skip tentacle orchestration
    python setup-project.py --dry-run               # Show what would be done
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = SCRIPT_DIR / "templates"
SKILLS_DIR = SCRIPT_DIR / "skills"
PRESETS_DIR = SCRIPT_DIR / "presets"

# Host metadata is centralised in host_manifest.py — import canonical constants.
# Do NOT add new hosts here; update host_manifest.py through the review process.
from host_manifest import HOST_INSTRUCTION_FILES as KNOWN_HOSTS_INSTRUCTION_FILES  # noqa: E402

# What to install
INSTALL_ITEMS = {
    # Skills (from tools/skills/ → .github/skills/)
    "skills": [
        {"src": "session-knowledge-creator", "label": "Session Knowledge Creator (meta-skill)"},
        {"src": "tentacle-creator", "label": "Tentacle Creator (meta-skill)"},
        {"src": "tentacle-orchestration", "label": "Tentacle Orchestration"},
        {"src": "agent-creator", "label": "Agent Creator (generates .agent.md files)"},
        {"src": "hook-creator", "label": "Hook Creator (quality enforcement hooks)"},
        {"src": "workflow-creator", "label": "Workflow Creator (phased development lifecycle)"},
        {"src": "find-skills", "label": "Find Skills (discover & install from skills.sh)"},
        {"src": "agent-instructions-auditor", "label": "Instructions Auditor (token budget, cache safety, quality)"},
        {"src": "forge-ecosystem", "label": "Forge Ecosystem (10 CLI tools for game & app dev)"},
        {"src": "code-reviewer", "label": "Code Reviewer (skeptical, signal-over-noise code review)"},
        {"src": "task-step-generator", "label": "Task Step Generator (structured step-file generation)"},
    ],
    # Templates (from tools/templates/ → .github/skills/ or .github/instructions/)
    "templates": [
        {
            "src": "SKILL.md",
            "dst": ".github/skills/session-knowledge/SKILL.md",
            "label": "Session Knowledge Skill",
        },
        {
            "src": "session-knowledge.instructions.md",
            "dst": ".github/instructions/session-knowledge.instructions.md",
            "label": "Session Knowledge Instructions (enforcement)",
        },
    ],
}

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

# Snippet to add to AGENTS.md
AGENTS_SNIPPET = """
## Pre-Task Briefing (BẮT BUỘC)

> **⚠️ MANDATORY**: Before starting a new task, run briefing to get context from past sessions.
> After fixing bugs or completing tasks, record learnings with proper categorization.
> Details: `.github/skills/session-knowledge/SKILL.md`

```bash
# Before task — get context
python3 ~/.copilot/tools/briefing.py "<task>" --full

# Before delegating to sub-agent — inject context
python3 ~/.copilot/tools/briefing.py "<sub-agent task>" --for-subagent

# During task — search for errors/topics
python3 ~/.copilot/tools/query-session.py "<error or topic>" --verbose

# After task — record with full metadata
python3 ~/.copilot/tools/learn.py --mistake "Title" "Description" --tags "t1,t2" --wing <wing> --room <room> --fact "key detail"
python3 ~/.copilot/tools/learn.py --pattern "Title" "Description" --tags "t1,t2" --wing <wing> --room <room>
python3 ~/.copilot/tools/learn.py --relate "#id1" "resolved_by" "#id2"
```
"""


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


def copy_if_changed(src: Path, dst: Path, dry_run: bool, label: str) -> bool:
    """Copy src to dst if content differs. Returns True if changed."""
    if not src.exists():
        print(f"  ⚠ Source not found: {src}")
        return False

    if dst.exists():
        src_hash = hashlib.md5(src.read_bytes()).hexdigest()
        dst_hash = hashlib.md5(dst.read_bytes()).hexdigest()
        if src_hash == dst_hash:
            print(f"  ⏭ {label} — already up to date")
            return False
        if dry_run:
            print(f"  [dry-run] Would update: {label}")
            return True
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"  ✓ Updated: {label}")
        return True

    if dry_run:
        print(f"  [dry-run] Would create: {label}")
        return True

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    print(f"  ✓ Installed: {label}")
    return True


def install_skills(project_root: Path, dry_run: bool) -> int:
    """Install creator skills from tools/skills/ → .github/skills/."""
    changes = 0
    for item in INSTALL_ITEMS["skills"]:
        src = SKILLS_DIR / item["src"] / "SKILL.md"
        dst = project_root / ".github" / "skills" / item["src"] / "SKILL.md"
        if copy_if_changed(src, dst, dry_run, item["label"]):
            changes += 1
    return changes


def install_templates(project_root: Path, dry_run: bool) -> int:
    """Install template files (SKILL.md, instructions) from tools/templates/."""
    changes = 0
    for item in INSTALL_ITEMS["templates"]:
        src = TEMPLATES_DIR / item["src"]
        dst = project_root / item["dst"]
        if copy_if_changed(src, dst, dry_run, item["label"]):
            changes += 1
    return changes


def install_tentacle(project_root: Path, dry_run: bool) -> int:
    """Install tentacle orchestration: .gitignore entry."""
    changes = 0
    gitignore = project_root / ".gitignore"

    if gitignore.exists():
        content = gitignore.read_text()
        if ".octogent/" not in content:
            if dry_run:
                print("  [dry-run] Would add .octogent/ to .gitignore")
            else:
                with open(gitignore, "a") as f:
                    f.write("\n# Tentacle orchestration (local work contexts)\n.octogent/\n")
                print("  ✓ Added .octogent/ to .gitignore")
            changes += 1
        else:
            print("  ⏭ .octogent/ already in .gitignore")
    else:
        if dry_run:
            print("  [dry-run] Would create .gitignore with .octogent/")
        else:
            gitignore.write_text("# Tentacle orchestration (local work contexts)\n.octogent/\n")
            print("  ✓ Created .gitignore with .octogent/")
        changes += 1

    return changes


def patch_claude_md(project_root: Path, dry_run: bool) -> bool:
    """Add Pre-Task Briefing section to CLAUDE.md if not present."""
    claude_md = project_root / KNOWN_HOSTS_INSTRUCTION_FILES["Claude Code"]
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
    instructions = project_root / KNOWN_HOSTS_INSTRUCTION_FILES["Copilot CLI"]
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


def patch_agents_md(project_root: Path, dry_run: bool) -> bool:
    """Add Pre-Task Briefing section to AGENTS.md if not present."""
    agents_md = project_root / KNOWN_HOSTS_INSTRUCTION_FILES["All agents"]
    if not agents_md.exists():
        print("  ⏭ No AGENTS.md found, skipping")
        return False

    content = agents_md.read_text()
    if "session-knowledge" in content or "briefing.py" in content:
        print("  ⏭ AGENTS.md already references session-knowledge")
        return False

    if dry_run:
        print("  [dry-run] Would add Pre-Task Briefing section to AGENTS.md")
        return True

    # Insert before "## Code Navigation" or "## Commands" or at end
    lines = content.split("\n")
    insert_idx = None

    for i, line in enumerate(lines):
        if line.startswith("## Code Navigation") or line.startswith("## Commands"):
            insert_idx = i
            break

    if insert_idx is None:
        insert_idx = len(lines)

    snippet_lines = AGENTS_SNIPPET.strip().split("\n")
    lines = lines[:insert_idx] + [""] + snippet_lines + [""] + lines[insert_idx:]
    agents_md.write_text("\n".join(lines))
    print("  ✓ Added Pre-Task Briefing section to AGENTS.md")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Integrate session-knowledge + tentacle orchestration into a project",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python setup-project.py                    # Full setup (auto-detect git root)
  python setup-project.py /path/to/project   # Explicit project root
  python setup-project.py --skill-only       # Skills only, no CLAUDE.md patching
  python setup-project.py --no-tentacle      # Skip tentacle orchestration
  python setup-project.py --profile python   # Install python hook bundle + WORKFLOW.md
  python setup-project.py --profile mobile   # Install mobile hook bundle + WORKFLOW.md
  python setup-project.py --dry-run          # Preview changes

Profiles (--profile):
  default     Minimal safe defaults (dangerous-blocker, secret-detector)
  python      Python project: TDD, test-reminder, build-reminder, commit-gate
  typescript  TypeScript/Node: coding-standards, test-reminder, build-reminder, commit-gate
  mobile      Android/iOS/KMP: architecture-guard, TDD, coding-standards, QA phase
  fullstack   Full-stack web: architecture-guard, coding-standards, session-banner

What gets installed:
  .github/skills/session-knowledge/SKILL.md          — Knowledge skill reference
  .github/skills/session-knowledge-creator/SKILL.md  — Meta-skill: customize for project
  .github/skills/tentacle-creator/SKILL.md           — Meta-skill: customize tentacle
  .github/skills/tentacle-orchestration/SKILL.md     — Tentacle workflow skill
  .github/instructions/session-knowledge.instructions.md — Enforcement (auto-inject)
  .gitignore                                         — Add .octogent/ entry
  CLAUDE.md / copilot-instructions.md / AGENTS.md    — Patched with references
  .github/hooks/*.sh                                 — Hook bundle (when --profile used)
  WORKFLOW.md                                        — Starter workflow (when --profile used)
"""
    )
    parser.add_argument(
        "project_root", nargs="?", default=None,
        help="Project root directory (default: auto-detect git root)"
    )
    parser.add_argument(
        "--skill-only", action="store_true",
        help="Only install skills and instructions, don't patch CLAUDE.md etc."
    )
    parser.add_argument(
        "--no-tentacle", action="store_true",
        help="Skip tentacle orchestration setup"
    )
    parser.add_argument(
        "--profile", default=None, metavar="PROFILE",
        help="Workflow profile to install as a hook bundle + WORKFLOW.md "
             "(default: none; choices: default, python, typescript, mobile, fullstack)"
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

    project_name = project_root.name
    print(f"🧠 Setting up AI knowledge tools for: {project_name}")
    print(f"   Path: {project_root}")
    if args.dry_run:
        print("   (dry-run mode)")
    print()

    changes = 0

    # 1. Install session-knowledge skill + instructions
    print("📋 Session Knowledge:")
    changes += install_templates(project_root, args.dry_run)

    # 2. Install creator skills (meta-skills)
    print("\n🔧 Creator Skills:")
    changes += install_skills(project_root, args.dry_run)

    # 3. Tentacle orchestration
    if not args.no_tentacle:
        print("\n🐙 Tentacle Orchestration:")
        changes += install_tentacle(project_root, args.dry_run)

    # 4. Patch config files
    if not args.skill_only:
        print("\n📝 Config Files:")
        if patch_claude_md(project_root, args.dry_run):
            changes += 1
        if patch_copilot_instructions(project_root, args.dry_run):
            changes += 1
        if patch_agents_md(project_root, args.dry_run):
            changes += 1

    # 5. Install workflow profile hook bundle (optional, only when --profile is given)
    if args.profile:
        import re as _re
        print(f"\n🔩 Hook Bundle ({args.profile} profile):")
        hook_installer = SCRIPT_DIR / "install-project-hooks.py"
        cmd = [sys.executable, str(hook_installer),
               "--profile", args.profile,
               "--project", str(project_root),
               "--workflow"]
        if args.dry_run:
            cmd.append("--dry-run")
        result = subprocess.run(cmd, capture_output=True, text=True)
        # Print installer output so the user sees progress.
        if result.stdout:
            print(result.stdout, end="")
        if result.returncode != 0:
            if result.stderr:
                print(result.stderr, end="")
            print(f"  ⚠  Hook bundle install exited with code {result.returncode}")
        else:
            # Parse the actual change count from installer output to avoid overcounting.
            # The installer prints "N change(s) applied." (real run) or
            # "Would make N change(s)." (dry-run); "No changes needed." → 0.
            m = _re.search(r"(\d+)\s+change\(s\)", result.stdout)
            if m:
                changes += int(m.group(1))

    # Summary
    print()
    if changes == 0:
        print("✅ Already set up — no changes needed.")
    elif args.dry_run:
        print(f"Would make {changes} change(s). Run without --dry-run to apply.")
    else:
        print(f"✅ Done! {changes} change(s) applied.")
        print()
        print("Next steps:")
        print("  1. Run: python3 ~/.copilot/tools/build-session-index.py --all")
        print("  2. Customize for your project:")
        print("     /session-knowledge-creator   — Generate project-specific knowledge skill")
        if not args.no_tentacle:
            print("     /tentacle-creator            — Generate project-specific tentacle skill")
        if args.profile:
            print(f"     Edit .github/hooks/*.sh      — Customize installed {args.profile} hooks")
        else:
            print("     --profile python|typescript|mobile|fullstack — Install hook bundle")
        print()
        print("How it works:")
        print("  📋 .instructions.md → auto-injected into EVERY AI context (enforcement)")
        print("  🔧 Creator skills  → run once to customize skills for your project")
        print("  🧠 briefing.py     → AI runs before each task (past mistakes/patterns)")
        print("  📝 learn.py        → AI records after each task (accumulate knowledge)")
        if not args.no_tentacle:
            print("  🐙 tentacle.py     → Multi-agent orchestration with scoped contexts")


if __name__ == "__main__":
    main()
