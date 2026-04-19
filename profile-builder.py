#!/usr/bin/env python3
"""
profile-builder.py — Build a custom workflow profile compatible with install-project-hooks.py.

Creates a JSON profile in presets/ (or a custom output directory) from CLI arguments or
interactive prompts.  The resulting file is immediately usable with:

    python3 install-project-hooks.py --profile <name>

Usage:
    # Non-interactive (all args supplied)
    python3 profile-builder.py \\
        --name myteam \\
        --description "My team workflow" \\
        --hooks dangerous-blocker.sh secret-detector.sh commit-gate.sh \\
        --phases CLARIFY BUILD TEST COMMIT

    # List available hooks / phases
    python3 profile-builder.py --list-hooks
    python3 profile-builder.py --list-phases

    # Dry-run (print JSON, do not write)
    python3 profile-builder.py --name myteam ... --dry-run

    # Overwrite an existing profile
    python3 profile-builder.py --name python ... --force

    # Write to a custom directory instead of presets/
    python3 profile-builder.py --name myteam ... --output-dir /some/dir

    # Skip hook-template validation (for profiles referencing non-shipped hooks)
    python3 profile-builder.py --name myteam ... --skip-hook-validation
"""

import argparse
import json
import os
import sys
from pathlib import Path

if os.name == "nt":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).resolve().parent
PRESETS_DIR = SCRIPT_DIR / "presets"
HOOK_TEMPLATES_DIR = SCRIPT_DIR / "skills" / "hook-creator" / "references"

KNOWN_PHASES = ["CLARIFY", "DESIGN", "VERIFY", "BUILD", "TEST", "REVIEW", "QA", "COMMIT"]

# Input validation limits (mirror conventions in codebase)
MAX_NAME_LEN = 64
MAX_DESC_LEN = 200
MAX_NOTES_LEN = 500


def available_hooks() -> list[str]:
    """Return hook template filenames available in the references directory."""
    if not HOOK_TEMPLATES_DIR.is_dir():
        return []
    return sorted(p.name for p in HOOK_TEMPLATES_DIR.glob("*.sh"))


def validate_profile(data: dict, skip_hook_validation: bool = False) -> list[str]:
    """Return a list of validation error strings (empty = valid)."""
    errors: list[str] = []

    # Required fields
    for field in ("name", "description", "hooks", "workflow_phases"):
        if field not in data:
            errors.append(f"Missing required field: '{field}'")

    name = data.get("name", "")
    if not name:
        errors.append("'name' must not be empty")
    elif len(name) > MAX_NAME_LEN:
        errors.append(f"'name' exceeds {MAX_NAME_LEN} characters")
    elif not all(c.isalnum() or c in "-_" for c in name):
        errors.append("'name' may only contain alphanumeric characters, hyphens, or underscores")

    desc = data.get("description", "")
    if not desc:
        errors.append("'description' must not be empty")
    elif len(desc) > MAX_DESC_LEN:
        errors.append(f"'description' exceeds {MAX_DESC_LEN} characters")

    hooks = data.get("hooks", [])
    if not isinstance(hooks, list) or len(hooks) == 0:
        errors.append("'hooks' must be a non-empty list")
    else:
        available = set(available_hooks())
        for hook in hooks:
            if not isinstance(hook, str) or not hook.endswith(".sh"):
                errors.append(f"Hook '{hook}' must be a .sh filename string")
            elif not skip_hook_validation and available and hook not in available:
                errors.append(
                    f"Hook template not found: '{hook}' "
                    f"(use --skip-hook-validation to bypass)"
                )

    phases = data.get("workflow_phases", [])
    if not isinstance(phases, list) or len(phases) == 0:
        errors.append("'workflow_phases' must be a non-empty list")
    else:
        unknown = [p for p in phases if p not in KNOWN_PHASES]
        if unknown:
            errors.append(
                f"Unknown phase(s): {', '.join(unknown)}. "
                f"Known: {', '.join(KNOWN_PHASES)}"
            )

    notes = data.get("workflow_notes", "")
    if notes and len(notes) > MAX_NOTES_LEN:
        errors.append(f"'workflow_notes' exceeds {MAX_NOTES_LEN} characters")

    return errors


def build_profile(
    name: str,
    description: str,
    hooks: list[str],
    phases: list[str],
    notes: str = "",
) -> dict:
    profile: dict = {
        "name": name,
        "description": description,
        "hooks": hooks,
        "workflow_phases": phases,
    }
    if notes:
        profile["workflow_notes"] = notes
    return profile


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a custom workflow profile for install-project-hooks.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python3 profile-builder.py --list-hooks
  python3 profile-builder.py --name myteam \\
      --description "My team workflow" \\
      --hooks dangerous-blocker.sh secret-detector.sh \\
      --phases CLARIFY BUILD TEST COMMIT
  python3 profile-builder.py --name myteam ... --dry-run
  python3 profile-builder.py --name myteam ... --force
""",
    )
    parser.add_argument("--name", help="Profile name (alphanumeric, hyphens, underscores)")
    parser.add_argument("--description", help="Short description of the profile")
    parser.add_argument("--hooks", nargs="+", metavar="HOOK",
                        help="Hook filenames (e.g. dangerous-blocker.sh commit-gate.sh)")
    parser.add_argument("--phases", nargs="+", metavar="PHASE",
                        help=f"Workflow phases from: {', '.join(KNOWN_PHASES)}")
    parser.add_argument("--notes", default="",
                        help="Optional workflow notes (shown in WORKFLOW.md)")
    parser.add_argument("--output-dir", default=None,
                        help="Directory to write profile JSON (default: presets/)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the generated JSON without writing any files")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite an existing profile with the same name")
    parser.add_argument("--skip-hook-validation", action="store_true",
                        help="Skip validation that referenced hook templates exist on disk")
    parser.add_argument("--list-hooks", action="store_true",
                        help="List available hook templates and exit")
    parser.add_argument("--list-phases", action="store_true",
                        help="List known workflow phases and exit")
    args = parser.parse_args()

    if args.list_hooks:
        hooks = available_hooks()
        if hooks:
            print("\nAvailable hook templates:\n")
            for h in hooks:
                print(f"  {h}")
        else:
            print(f"  (none — templates dir not found: {HOOK_TEMPLATES_DIR})")
        print()
        return

    if args.list_phases:
        print("\nKnown workflow phases (in typical order):\n")
        for p in KNOWN_PHASES:
            print(f"  {p}")
        print()
        return

    # Require all build args
    missing = []
    if not args.name:
        missing.append("--name")
    if not args.description:
        missing.append("--description")
    if not args.hooks:
        missing.append("--hooks")
    if not args.phases:
        missing.append("--phases")
    if missing:
        print(f"✗ Missing required argument(s): {', '.join(missing)}")
        print("  Run with --help for usage.")
        sys.exit(1)

    profile = build_profile(
        name=args.name,
        description=args.description,
        hooks=args.hooks,
        phases=args.phases,
        notes=args.notes,
    )

    errors = validate_profile(profile, skip_hook_validation=args.skip_hook_validation)
    if errors:
        print("✗ Profile validation failed:")
        for e in errors:
            print(f"  • {e}")
        sys.exit(1)

    out_dir = Path(args.output_dir).resolve() if args.output_dir else PRESETS_DIR
    out_path = out_dir / f"{args.name}.json"

    if args.dry_run:
        print(f"[dry-run] Would write: {out_path}\n")
        print(json.dumps(profile, indent=2))
        return

    if out_path.exists() and not args.force:
        print(f"✗ Profile '{args.name}' already exists at {out_path}")
        print("  Use --force to overwrite.")
        sys.exit(1)

    out_dir.mkdir(parents=True, exist_ok=True)
    already_exists = out_path.exists()
    out_path.write_text(json.dumps(profile, indent=2) + "\n")

    action = "Overwritten" if already_exists else "Created"
    print(f"✓ {action}: {out_path}")
    print(f"  Profile '{args.name}' is ready for use:")
    print(f"  python3 install-project-hooks.py --profile {args.name}")


if __name__ == "__main__":
    main()
