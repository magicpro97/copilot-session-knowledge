#!/usr/bin/env python3
"""
profile-export.py — Export workflow profiles to portable JSON files.

Exports one or all profiles from presets/ to a file or directory.
The output format is compatible with profile-import.py and can also be loaded
directly by install-project-hooks.py (single-profile plain-JSON export).

Two export formats:
  plain   — the profile JSON as-is (importable directly by install-project-hooks.py)
  bundle  — a wrapper JSON with metadata and one or more profiles embedded

Usage:
    # Export one profile as plain JSON
    python3 profile-export.py --profile python --output python-profile.json

    # Export one profile as a bundle (with metadata)
    python3 profile-export.py --profile python --output python.bundle.json --format bundle

    # Export all profiles into a directory (one plain .json per profile)
    python3 profile-export.py --all --output-dir ./exported-profiles/

    # Export all profiles as a single bundle file
    python3 profile-export.py --all --output all-profiles.bundle.json --format bundle

    # Dry-run: print what would be written
    python3 profile-export.py --profile python --output out.json --dry-run

    # Override source presets directory
    python3 profile-export.py --profile python --presets-dir /custom/presets --output out.json
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

if os.name == "nt":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).resolve().parent
PRESETS_DIR = SCRIPT_DIR / "presets"

BUNDLE_VERSION = "1.0"
BUNDLE_TOOL = "profile-export.py"

REQUIRED_FIELDS = {"name", "description", "hooks", "workflow_phases"}


def load_profile(name: str, presets_dir: Path) -> dict:
    path = presets_dir / f"{name}.json"
    if not path.exists():
        available = sorted(p.stem for p in presets_dir.glob("*.json"))
        raise FileNotFoundError(
            f"Profile '{name}' not found in {presets_dir}. "
            f"Available: {', '.join(available) or '(none)'}"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def load_all_profiles(presets_dir: Path) -> list[dict]:
    profiles = []
    for p in sorted(presets_dir.glob("*.json")):
        profiles.append(json.loads(p.read_text(encoding="utf-8")))
    return profiles


def make_bundle(profiles: list[dict]) -> dict:
    return {
        "exported_by": BUNDLE_TOOL,
        "bundle_version": BUNDLE_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "profile_count": len(profiles),
        "profiles": profiles,
    }


def is_bundle(data: dict) -> bool:
    return "profiles" in data and "exported_by" in data


def write_json(path: Path, data: dict, dry_run: bool) -> None:
    text = json.dumps(data, indent=2) + "\n"
    if dry_run:
        print(f"  [dry-run] Would write {path} ({len(text)} bytes)")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"  ✓  Written: {path}")


def export_single(profile: dict, output: Path, fmt: str, dry_run: bool) -> None:
    if fmt == "bundle":
        data = make_bundle([profile])
    else:
        data = profile
    write_json(output, data, dry_run)


def export_all_to_dir(profiles: list[dict], output_dir: Path, fmt: str, dry_run: bool) -> None:
    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
    for profile in profiles:
        name = profile.get("name", "unknown")
        if fmt == "bundle":
            out = output_dir / f"{name}.bundle.json"
            data = make_bundle([profile])
        else:
            out = output_dir / f"{name}.json"
            data = profile
        write_json(out, data, dry_run)


def export_all_to_bundle(profiles: list[dict], output: Path, dry_run: bool) -> None:
    data = make_bundle(profiles)
    write_json(output, data, dry_run)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export workflow profiles to portable JSON files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python3 profile-export.py --profile python --output python.json
  python3 profile-export.py --profile python --output python.bundle.json --format bundle
  python3 profile-export.py --all --output-dir ./exported/
  python3 profile-export.py --all --output all.bundle.json --format bundle
""",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--profile", metavar="NAME",
                       help="Export a single named profile")
    group.add_argument("--all", action="store_true",
                       help="Export all profiles")

    parser.add_argument("--output", metavar="PATH",
                        help="Output file path (for single profile or --all + --format bundle)")
    parser.add_argument("--output-dir", metavar="DIR",
                        help="Output directory for --all (one file per profile)")
    parser.add_argument("--format", choices=["plain", "bundle"], default="plain",
                        help="plain = raw JSON (default), bundle = metadata wrapper")
    parser.add_argument("--presets-dir", default=None, metavar="DIR",
                        help=f"Presets directory (default: {PRESETS_DIR})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be written without writing")
    args = parser.parse_args()

    presets_dir = Path(args.presets_dir).resolve() if args.presets_dir else PRESETS_DIR
    if not presets_dir.is_dir():
        print(f"✗ Presets directory not found: {presets_dir}")
        sys.exit(1)

    # ── Single-profile export ────────────────────────────────────────────────
    if args.profile:
        try:
            profile = load_profile(args.profile, presets_dir)
        except FileNotFoundError as e:
            print(f"✗ {e}")
            sys.exit(1)

        if not args.output:
            ext = ".bundle.json" if args.format == "bundle" else ".json"
            args.output = f"{args.profile}{ext}"

        print(f"📦 Exporting profile '{args.profile}' → {args.output}")
        export_single(profile, Path(args.output), args.format, args.dry_run)
        if not args.dry_run:
            print(f"\n✅ Done. Import with:")
            print(f"   python3 profile-import.py --file {args.output}")
        return

    # ── All-profiles export ───────────────────────────────────────────────────
    profiles = load_all_profiles(presets_dir)
    if not profiles:
        print(f"✗ No profiles found in {presets_dir}")
        sys.exit(1)

    if args.output:
        # All → single bundle file
        if args.format != "bundle":
            print("✗ --output with --all requires --format bundle "
                  "(use --output-dir for plain per-profile export)")
            sys.exit(1)
        print(f"📦 Exporting {len(profiles)} profile(s) → {args.output}")
        export_all_to_bundle(profiles, Path(args.output), args.dry_run)
        if not args.dry_run:
            print(f"\n✅ Done. Import with:")
            print(f"   python3 profile-import.py --file {args.output}")
    else:
        # All → directory
        out_dir = Path(args.output_dir) if args.output_dir else Path(".")
        print(f"📦 Exporting {len(profiles)} profile(s) → {out_dir}/")
        export_all_to_dir(profiles, out_dir, args.format, args.dry_run)
        if not args.dry_run:
            print(f"\n✅ Done.")


if __name__ == "__main__":
    main()
