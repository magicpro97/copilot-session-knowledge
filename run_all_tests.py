#!/usr/bin/env python3
"""run_all_tests.py — stdlib-only test runner.

Discovers test_*.py files in repo root and subdirs (excluding .octogent/,
tests/fixtures/), runs each via subprocess, aggregates results.

Usage:
    python3 run_all_tests.py          # run all discovered tests
    python3 run_all_tests.py --help   # show usage
    python3 run_all_tests.py --dry    # list test files without running

Exits 0 if all pass, 1 if any fail. Total wall-time budget: 5 minutes.

Note: This runner covers Python tests only.  To mirror CI fully, also run the
browse-ui quality gates from the browse-ui/ directory:

    pnpm typecheck   # TypeScript type checking
    pnpm lint        # ESLint
    pnpm test        # Vitest unit tests
    pnpm build       # Next.js static build

E2E (Playwright) is gated to manual CI runs; local use:
    pnpm test:e2e
"""

import os
import subprocess
import sys
import time
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent
EXCLUDE_DIRS = {".octogent", "__pycache__", ".git", ".venv", "venv"}
EXCLUDE_PATH_PARTS = {"fixtures"}  # skip tests/fixtures/
TIMEOUT_PER_TEST = 60  # seconds
TOTAL_BUDGET = 300  # 5 minutes


def discover_tests(root: Path) -> list[Path]:
    """Return sorted list of test_*.py files under root."""
    found = []
    for f in sorted(root.rglob("test_*.py")):
        try:
            rel_parts = f.relative_to(root).parts
        except ValueError:
            rel_parts = f.parts
        # Skip excluded dirs
        if set(rel_parts) & EXCLUDE_DIRS:
            continue
        # Skip hidden dirs within relative path only
        if any(p.startswith(".") for p in rel_parts[:-1]):
            continue
        # Skip fixtures subdir
        if EXCLUDE_PATH_PARTS & set(rel_parts):
            continue
        found.append(f)
    return found


def run_test(test_file: Path) -> tuple[bool, float, str]:
    """Run a single test file. Returns (passed, duration_secs, output)."""
    start = time.monotonic()
    try:
        result = subprocess.run(
            [sys.executable, str(test_file)],
            timeout=TIMEOUT_PER_TEST,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        duration = time.monotonic() - start
        passed = result.returncode == 0
        output = result.stdout + result.stderr
        return passed, duration, output
    except subprocess.TimeoutExpired:
        duration = time.monotonic() - start
        return False, duration, f"TIMEOUT after {TIMEOUT_PER_TEST}s"
    except Exception as exc:
        duration = time.monotonic() - start
        return False, duration, f"ERROR: {exc}"


def format_row(name: str, status: str, duration: float) -> str:
    return f"  {name:<55} {status:<8} {duration:6.1f}s"


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]

    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0

    dry_run = "--dry" in argv

    tests = discover_tests(REPO)
    if not tests:
        print("No test_*.py files found.")
        return 0

    print(f"Discovered {len(tests)} test file(s).\n")
    print(f"  {'Test':<55} {'Status':<8} {'Time':>6}")
    print("  " + "-" * 72)

    if dry_run:
        for t in tests:
            print(f"  {t.name:<55} {'(dry)':8} {'':>6}")
        return 0

    passed = failed = 0
    wall_start = time.monotonic()
    failures: list[tuple[str, str]] = []

    for test_file in tests:
        elapsed_total = time.monotonic() - wall_start
        if elapsed_total >= TOTAL_BUDGET:
            print(f"\n⚠  Budget exceeded ({TOTAL_BUDGET}s). Remaining tests skipped.")
            break

        ok, dur, output = run_test(test_file)
        status = "PASS" if ok else "FAIL"
        print(format_row(test_file.name, status, dur))

        if ok:
            passed += 1
        else:
            failed += 1
            failures.append((test_file.name, output))

    total = passed + failed
    wall = time.monotonic() - wall_start
    print("\n" + "=" * 72)
    print(f"Results: {passed}/{total} passed in {wall:.1f}s")

    if failures:
        print(f"\n{'─'*72}")
        print("FAILURES:")
        for name, out in failures:
            print(f"\n  ▶ {name}")
            # Show last 20 lines of output
            lines = out.strip().splitlines()
            for line in lines[-20:]:
                print(f"    {line}")

    if failed:
        print(f"\n❌ {failed} test(s) failed.")
        return 1

    print("✅ All tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
