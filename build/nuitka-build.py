#!/usr/bin/env python3
"""
Build sk as a standalone executable using Nuitka.

Usage:
    python build/nuitka-build.py [--onefile] [--output-dir dist/]

Requirements:
    - Python 3.12+
    - Nuitka: pip install nuitka
    - C compiler: MSVC (Windows), gcc/clang (Linux/macOS)
"""

import os
import subprocess
import sys
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent.resolve()
ENTRY_POINT = REPO_ROOT / "sk.py"

# All scripts that sk.py dispatches (must be included so runpy can find them)
DISPATCHED_SCRIPTS = [
    "briefing.py",
    "query-session.py",
    "learn.py",
    "tentacle.py",
    "install.py",
    "setup-project.py",
    "auto-update-tools.py",
    "browse.py",
    "benchmark.py",
    "retro.py",
    "copilot-cli-healer.py",
    "build-session-index.py",
    "extract-knowledge.py",
    "migrate.py",
    "index-status.py",
    "knowledge-health.py",
    "embed.py",
    "sync-daemon.py",
    "sync-config.py",
    "sync-status.py",
    "sync-gateway.py",
    "sync-knowledge.py",
    "checkpoint-save.py",
    "checkpoint-restore.py",
    "checkpoint-diff.py",
    "profile-builder.py",
    "profile-import.py",
    "profile-export.py",
    "project-context.py",
    "codebase-map.py",
    "trend-scout.py",
    "scout-config.py",
    "scout-status.py",
]

# Packages to include as source (compiled alongside sk.py)
INCLUDE_PACKAGES = [
    "browse",
    "hooks",
]

# Data directories that must be bundled (static assets, not Python code)
DATA_DIRS = [
    ("browse/static", "browse/static"),
]

# Modules to exclude (optional heavy deps, test frameworks)
NOFOLLOW_IMPORTS = [
    "scikit-learn",
    "sklearn",
    "numpy",
    "scipy",
    "pandas",
    "tkinter",
    "test",
    "unittest",
    "pytest",
    "setuptools",
    "pip",
    "distutils",
]


def build_nuitka_command(*, onefile: bool = True, output_dir: str = "dist") -> list[str]:
    """Assemble the Nuitka compilation command."""
    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        "--standalone",
    ]

    if onefile:
        cmd.append("--onefile")

    # Output directory
    cmd.extend(["--output-dir", output_dir])

    # Binary name
    cmd.extend(["--output-filename", "sk.exe" if os.name == "nt" else "sk"])

    # Include all dispatched scripts as data files (runpy needs the .py source)
    for script in DISPATCHED_SCRIPTS:
        script_path = REPO_ROOT / script
        if script_path.exists():
            cmd.extend(["--include-data-files", f"{script_path}={script}"])

    # Include packages (browse/, hooks/)
    for pkg in INCLUDE_PACKAGES:
        pkg_path = REPO_ROOT / pkg
        if pkg_path.exists():
            cmd.extend(["--include-package", pkg])

    # Include data directories (static assets)
    for src_rel, dst_rel in DATA_DIRS:
        src_abs = REPO_ROOT / src_rel
        if src_abs.exists():
            cmd.extend(["--include-data-dir", f"{src_abs}={dst_rel}"])

    # Exclude heavy optional dependencies
    for mod in NOFOLLOW_IMPORTS:
        cmd.extend(["--nofollow-import-to", mod])

    # Performance and size optimizations
    cmd.extend([
        "--assume-yes-for-downloads",  # Auto-download dependency walkers
        "--remove-output",             # Clean previous build artifacts
        "--no-pyi-file",               # Skip .pyi stub generation
    ])

    # Python flags for the compiled binary
    cmd.extend(["--python-flag", "-O"])  # Optimize bytecode

    # Add the entry point last
    cmd.append(str(ENTRY_POINT))

    return cmd


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build sk executable with Nuitka")
    parser.add_argument(
        "--onefile",
        action="store_true",
        default=True,
        help="Build as single file (default: True)",
    )
    parser.add_argument(
        "--no-onefile",
        action="store_true",
        help="Build as directory (multiple files)",
    )
    parser.add_argument(
        "--output-dir",
        default="dist",
        help="Output directory (default: dist/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the command without executing",
    )
    args = parser.parse_args()

    onefile = not args.no_onefile
    cmd = build_nuitka_command(onefile=onefile, output_dir=args.output_dir)

    print(f"{'[DRY RUN] ' if args.dry_run else ''}Nuitka build command:")
    print(f"  {' '.join(cmd)}")
    print()

    if args.dry_run:
        return 0

    # Verify Nuitka is installed
    try:
        subprocess.run(
            [sys.executable, "-m", "nuitka", "--version"],
            capture_output=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("ERROR: Nuitka is not installed. Run: pip install nuitka", file=sys.stderr)
        return 1

    print(f"Building sk executable ({'onefile' if onefile else 'standalone'})...")
    print(f"Entry point: {ENTRY_POINT}")
    print(f"Output: {args.output_dir}/")
    print()

    result = subprocess.run(cmd, cwd=str(REPO_ROOT))

    if result.returncode == 0:
        exe_name = "sk.exe" if os.name == "nt" else "sk"
        exe_path = Path(args.output_dir) / exe_name
        if exe_path.exists():
            size_mb = exe_path.stat().st_size / (1024 * 1024)
            print(f"\n✅ Build successful: {exe_path} ({size_mb:.1f} MB)")
        else:
            print(f"\n✅ Build completed. Check {args.output_dir}/ for output.")
    else:
        print(f"\n❌ Build failed with exit code {result.returncode}", file=sys.stderr)

    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
