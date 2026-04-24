#!/usr/bin/env python3
"""
profile-import.py — Import workflow profiles into presets/ from exported JSON files.

Accepts:
  - Plain profile JSON (as exported by `profile-export.py --format plain` or hand-crafted)
  - Bundle JSON (as exported by `profile-export.py --format bundle`)

Safety guarantees:
  - Validates required fields and schema before writing.
  - Warns when referenced hook templates do not exist (skippable with --skip-hook-validation).
  - Refuses to overwrite existing profiles unless --force is given.
  - Refuses to import profiles whose names collide with shipped profiles unless --force.
  - Validates all phases against the known set.
  - Input truncated / sanitised (name ≤ 64 chars, only alphanumeric/-/_).

Usage:
    # Import a single profile JSON
    python3 profile-import.py --file python-custom.json

    # Import a bundle (all profiles inside it)
    python3 profile-import.py --file all-profiles.bundle.json

    # Import only a specific profile from a bundle
    python3 profile-import.py --file all-profiles.bundle.json --name python

    # Overwrite existing profile
    python3 profile-import.py --file custom.json --force

    # Dry-run: validate only, do not write
    python3 profile-import.py --file custom.json --dry-run

    # Import into a custom presets directory
    python3 profile-import.py --file custom.json --presets-dir /custom/presets

    # Skip hook-template validation (for profiles with non-shipped hooks)
    python3 profile-import.py --file custom.json --skip-hook-validation
"""

import argparse
import json
import os
import sys
from pathlib import Path

if os.name == "nt":
    import io
import os
import sys
if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).resolve().parent
PRESETS_DIR = SCRIPT_DIR / "presets"
HOOK_TEMPLATES_DIR = SCRIPT_DIR / "skills" / "hook-creator" / "references"

KNOWN_PHASES = {"CLARIFY", "DESIGN", "VERIFY", "BUILD", "TEST", "REVIEW", "QA", "COMMIT"}
REQUIRED_FIELDS = {"name", "description", "hooks", "workflow_phases"}

MAX_NAME_LEN = 64
MAX_DESC_LEN = 200
MAX_NOTES_LEN = 500

# Profiles shipped with the tool — extra confirmation prompt when overwriting
SHIPPED_PROFILES = {"default", "python", "typescript", "mobile", "fullstack"}


def available_hook_templates() -> set[str]:
    if not HOOK_TEMPLATES_DIR.is_dir():
        return set()
    return {p.name for p in HOOK_TEMPLATES_DIR.glob("*.sh")}


def validate_profile(data: dict, skip_hook_validation: bool = False) -> list[str]:
    """Return list of validation error strings (empty = valid)."""
    errors: list[str] = []

    if not isinstance(data, dict):
        return ["Profile must be a JSON object"]

    missing = REQUIRED_FIELDS - set(data.keys())
    for field in sorted(missing):
        errors.append(f"Missing required field: '{field}'")

    name = data.get("name", "")
    if not isinstance(name, str) or not name:
        errors.append("'name' must be a non-empty string")
    elif len(name) > MAX_NAME_LEN:
        errors.append(f"'name' exceeds {MAX_NAME_LEN} characters")
    elif not all(c.isalnum() or c in "-_" for c in name):
        errors.append(
            "'name' may only contain alphanumeric characters, hyphens, or underscores"
        )

    desc = data.get("description", "")
    if not isinstance(desc, str) or not desc:
        errors.append("'description' must be a non-empty string")
    elif len(desc) > MAX_DESC_LEN:
        errors.append(f"'description' exceeds {MAX_DESC_LEN} characters")

    hooks = data.get("hooks", [])
    if not isinstance(hooks, list) or len(hooks) == 0:
        errors.append("'hooks' must be a non-empty list")
    else:
        available = available_hook_templates()
        for hook in hooks:
            if not isinstance(hook, str) or not hook.endswith(".sh"):
                errors.append(f"Hook entry must be a .sh filename string, got: {hook!r}")
            elif not skip_hook_validation and available and hook not in available:
                errors.append(
                    f"Hook template not found on disk: '{hook}' "
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
                f"Known: {', '.join(sorted(KNOWN_PHASES))}"
            )

    notes = data.get("workflow_notes", "")
    if notes and len(notes) > MAX_NOTES_LEN:
        errors.append(f"'workflow_notes' exceeds {MAX_NOTES_LEN} characters")

    return errors


def is_bundle(data: dict) -> bool:
    return isinstance(data, dict) and "profiles" in data and "exported_by" in data


def import_profile(
    profile: dict,
    presets_dir: Path,
    force: bool,
    dry_run: bool,
    skip_hook_validation: bool,
) -> bool:
    """Validate and write one profile. Returns True on success."""
    errors = validate_profile(profile, skip_hook_validation=skip_hook_validation)
    name = profile.get("name", "<unknown>")

    if errors:
        print(f"  ✗ '{name}' failed validation:")
        for e in errors:
            print(f"      • {e}")
        return False

    dest = presets_dir / f"{name}.json"

    if dest.exists() and not force:
        shipped_note = " (shipped profile)" if name in SHIPPED_PROFILES else ""
        print(
            f"  ✗ '{name}'{shipped_note} already exists at {dest} — "
            f"use --force to overwrite"
        )
        return False

    hooks = profile.get("hooks", [])
    phases = profile.get("workflow_phases", [])
    action = "Would overwrite" if dest.exists() else "Would import"

    if dry_run:
        print(f"  [dry-run] {action}: '{name}' "
              f"({len(hooks)} hooks, {len(phases)} phases) → {dest}")
        return True

    presets_dir.mkdir(parents=True, exist_ok=True)
    already_exists = dest.exists()
    dest.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    action = "Overwritten" if already_exists else "Imported"
    print(f"  ✓  {action}: '{name}' ({len(hooks)} hooks, {len(phases)} phases) → {dest}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import workflow profiles into presets/ from exported JSON files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python3 profile-import.py --file custom-profile.json
  python3 profile-import.py --file all-profiles.bundle.json
  python3 profile-import.py --file bundle.json --name python
  python3 profile-import.py --file custom.json --force
  python3 profile-import.py --file custom.json --dry-run
  python3 profile-import.py --file custom.json --skip-hook-validation
""",
    )
    parser.add_argument("--file", required=True, metavar="PATH",
                        help="Path to profile JSON or bundle JSON file to import")
    parser.add_argument("--name", default=None, metavar="NAME",
                        help="Import only this profile name (for bundle files with multiple profiles)")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing profiles (including shipped presets)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate and show what would be imported without writing")
    parser.add_argument("--skip-hook-validation", action="store_true",
                        help="Skip validation that referenced hook templates exist on disk")
    parser.add_argument("--presets-dir", default=None, metavar="DIR",
                        help=f"Destination presets directory (default: {PRESETS_DIR})")
    args = parser.parse_args()

    src = Path(args.file)
    if not src.exists():
        print(f"✗ File not found: {src}")
        sys.exit(1)

    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"✗ Invalid JSON in {src}: {e}")
        sys.exit(1)

    presets_dir = Path(args.presets_dir).resolve() if args.presets_dir else PRESETS_DIR

    if args.dry_run:
        print(f"[dry-run] Importing from {src} → {presets_dir}\n")
    else:
        print(f"📥 Importing from {src} → {presets_dir}\n")

    # Determine which profiles to import
    if is_bundle(data):
        all_profiles: list[dict] = data.get("profiles", [])
        exported_at = data.get("exported_at", "unknown")
        print(f"  Bundle: {len(all_profiles)} profile(s) exported at {exported_at}")
        if args.name:
            all_profiles = [p for p in all_profiles if p.get("name") == args.name]
            if not all_profiles:
                print(f"  ✗ Profile '{args.name}' not found in bundle")
                sys.exit(1)
        print()
    else:
        all_profiles = [data]
        if args.name and data.get("name") != args.name:
            print(f"  ✗ File contains profile '{data.get('name')}', "
                  f"not '{args.name}'")
            sys.exit(1)

    success = 0
    failed = 0
    for profile in all_profiles:
        ok = import_profile(
            profile=profile,
            presets_dir=presets_dir,
            force=args.force,
            dry_run=args.dry_run,
            skip_hook_validation=args.skip_hook_validation,
        )
        if ok:
            success += 1
        else:
            failed += 1

    print()
    if failed:
        print(f"⚠  {success} imported, {failed} failed.")
        sys.exit(1)
    elif args.dry_run:
        print(f"✅ Dry-run: {success} profile(s) would be imported.")
    else:
        print(f"✅ {success} profile(s) imported successfully.")
        if success:
            print("\nVerify with:")
            print("  python3 install-project-hooks.py --list-profiles")


if __name__ == "__main__":
    main()
