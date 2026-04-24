#!/usr/bin/env python3
"""check_syntax.py — stdlib-only syntax checker.

Walks the repo (or given paths) and runs py_compile on every .py file.
Prints file:line: error for each failure. Exits 0 if all OK, 1 otherwise.

Usage:
    python3 scripts/check_syntax.py              # check entire repo
    python3 scripts/check_syntax.py path/a.py    # targeted check
    python3 scripts/check_syntax.py src/ tests/  # check directories
"""

import os
import py_compile
import sys
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

EXCLUDE_DIRS = {".octogent", "__pycache__", ".git", ".venv", "venv"}


def _iter_py_files(root: Path):
    """Yield all .py files under root, skipping excluded directories.

    Exclusion rules:
    - Any directory listed in EXCLUDE_DIRS is skipped entirely.
    - Any file whose name ends in '.py.txt' is skipped (fixture stubs).
    - Any .py file under a 'fixtures/' directory is skipped.
    - Hidden directories (dot-prefixed) within the relative path are skipped.
    """
    root = root.resolve()
    for entry in sorted(root.rglob("*.py")):
        try:
            rel_parts = entry.relative_to(root).parts
        except ValueError:
            rel_parts = entry.parts
        if set(rel_parts) & EXCLUDE_DIRS:
            continue
        # Skip files in any 'fixtures' subdirectory
        if "fixtures" in rel_parts[:-1]:
            continue
        # Skip hidden dirs only within the relative path
        if any(p.startswith(".") for p in rel_parts[:-1]):
            continue
        yield entry


def check_path(target: Path) -> list[tuple[Path, str]]:
    """Return list of (file, error_message) for files that fail py_compile."""
    failures = []
    if target.is_file():
        files = [target] if target.suffix == ".py" else []
    else:
        files = list(_iter_py_files(target))

    for f in files:
        try:
            py_compile.compile(str(f), doraise=True)
        except py_compile.PyCompileError as exc:
            failures.append((f, str(exc)))
    return failures


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]

    repo_root = Path(__file__).resolve().parent.parent
    targets = [Path(a) for a in argv] if argv else [repo_root]

    all_failures = []
    for t in targets:
        all_failures.extend(check_path(t))

    if all_failures:
        for f, msg in all_failures:
            # Normalise to file:line: error format
            print(msg.strip(), file=sys.stderr)
        print(f"\n{len(all_failures)} file(s) with syntax error(s).", file=sys.stderr)
        return 1

    print(f"Syntax OK — checked {sum(len(list(_iter_py_files(t))) if t.is_dir() else 1 for t in targets)} file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
