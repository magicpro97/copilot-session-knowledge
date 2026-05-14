#!/usr/bin/env python3
"""
skill-patch.py — Targeted patch tool for SKILL.md files.

Performs fuzzy-whitespace-tolerant targeted replacement inside a SKILL.md,
writes atomically via a tempfile + os.replace, re-validates with
validate-skill.py, and logs the patch to skill-metrics.db.

Usage:
    python skill-patch.py <path> --old "old text" --new "new text" [options]

Arguments:
    path                Path to SKILL.md or skill directory

Options:
    --old TEXT          Text to find (fuzzy whitespace / indentation matching)
    --new TEXT          Replacement text
    --replace-all       Replace all occurrences (default: first occurrence only)
    --dry-run           Show diff; do NOT write or log anything
    --no-validate       Skip post-patch validation via validate-skill.py
    --no-metrics        Skip logging patch history to skill-metrics.db
    --metrics-db PATH   Override the skill-metrics.db path
    -h, --help          Show this help and exit

Exit codes:
    0   Patch applied (and, if not --no-validate, skill is valid)
    1   Patch failed (no match found or validation errors)
    2   Usage error
"""

import argparse
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SESSION_STATE = Path.home() / ".copilot" / "session-state"
DEFAULT_METRICS_DB = SESSION_STATE / "skill-metrics.db"

_SKILL_PATCH_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS skill_patch_history (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_path           TEXT NOT NULL,
    patched_at           TEXT NOT NULL,
    old_text             TEXT NOT NULL,
    new_text             TEXT NOT NULL,
    occurrences_replaced INTEGER NOT NULL DEFAULT 0,
    replace_all          INTEGER NOT NULL DEFAULT 0,
    dry_run              INTEGER NOT NULL DEFAULT 0,
    validation_passed    INTEGER,
    validation_errors    INTEGER NOT NULL DEFAULT 0,
    validation_warnings  INTEGER NOT NULL DEFAULT 0
);
"""


# ---------------------------------------------------------------------------
# Fuzzy-whitespace matching
# ---------------------------------------------------------------------------

def _fuzzy_pattern(text: str) -> re.Pattern:
    """
    Build a regex that matches *text* modulo leading/trailing whitespace on
    each line and collapsed internal whitespace runs.

    Strategy:
    - Split on any whitespace token (\\s+) so the same logical content matches
      regardless of extra spaces, tabs, or mixed indent levels.
    - Each non-empty token is re.escape'd so literal characters are not
      misinterpreted as regex metacharacters.
    - Tokens are joined with \\s+ so any whitespace run between tokens matches.
    - A leading/trailing \\s* allows the overall match to consume surrounding
      whitespace that belongs to the indent/newline context, which the caller
      then compensates for when building the replacement string.
    """
    tokens = [re.escape(t) for t in text.split() if t]
    if not tokens:
        raise ValueError("--old text contains only whitespace; nothing to match")
    return re.compile(r"\s*" + r"\s+".join(tokens) + r"\s*", re.MULTILINE)


def find_occurrences(content: str, old_text: str) -> list[re.Match]:
    """Return a list of all non-overlapping matches of old_text (fuzzy) in content."""
    pat = _fuzzy_pattern(old_text)
    return list(pat.finditer(content))


def apply_patch(
    content: str,
    old_text: str,
    new_text: str,
    *,
    replace_all: bool = False,
) -> tuple[str, int]:
    """
    Apply targeted replacement and return (patched_content, occurrences_replaced).

    The replacement preserves the *leading* whitespace of each match so that
    indentation is kept intact.  The matched span (including surrounding
    whitespace captured by the fuzzy pattern) is replaced by new_text after
    re-applying the leading whitespace of the original match.
    """
    pat = _fuzzy_pattern(old_text)
    replaced = 0

    def _replacer(m: re.Match) -> str:
        nonlocal replaced
        replaced += 1
        # Determine leading whitespace of the match (newline + indent)
        matched = m.group(0)
        # Preserve leading whitespace from the matched span.
        # Match a leading newline+indent first (block-level), then fall back
        # to plain spaces/tabs (mid-line match like "1. Step one").
        leading_ws_m = re.match(r"([\r\n][ \t]*|[ \t]+)", matched)
        prefix = leading_ws_m.group(1) if leading_ws_m else ""
        # Preserve any trailing newline from the matched span
        trailing_ws_m = re.search(r"([\r\n][ \t]*)$", matched)
        suffix = trailing_ws_m.group(1) if trailing_ws_m else ""
        return prefix + new_text + suffix

    if replace_all:
        result = pat.sub(_replacer, content)
    else:
        result, n = pat.subn(_replacer, content, count=1)
        replaced = n

    return result, replaced


# ---------------------------------------------------------------------------
# Skill path resolution
# ---------------------------------------------------------------------------

def resolve_skill_path(raw: str) -> Path:
    p = Path(raw).expanduser().resolve()
    if p.is_dir():
        candidate = p / "SKILL.md"
        if not candidate.exists():
            raise FileNotFoundError(f"No SKILL.md found in directory: {p}")
        return candidate
    if not p.exists():
        raise FileNotFoundError(f"SKILL.md not found: {p}")
    return p


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------

def atomic_write(path: Path, content: str) -> None:
    """Write *content* to *path* atomically via a sibling tempfile + os.replace."""
    dir_ = path.parent
    fd, tmp_path = tempfile.mkstemp(prefix=".skill-patch-", suffix=".tmp", dir=dir_)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _write_to_temp(target_path: Path, content: str) -> Path:
    """Write *content* to a sibling temp file and return its path.

    The caller is responsible for either committing (os.replace) or
    discarding (unlink) the temp file — the original *target_path* is
    never touched by this function.
    """
    dir_ = target_path.parent
    fd, tmp_str = tempfile.mkstemp(prefix=".skill-patch-", suffix=".tmp", dir=dir_)
    tmp_path = Path(tmp_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return tmp_path


# ---------------------------------------------------------------------------
# Post-patch validation
# ---------------------------------------------------------------------------

def run_validation(skill_path: Path) -> tuple[bool, int, int]:
    """
    Run validate-skill.py against *skill_path* via subprocess.

    Returns (passed: bool, errors: int, warnings: int).
    Fails open on import/subprocess errors.
    """
    validate_script = Path(__file__).parent.resolve() / "validate-skill.py"
    if not validate_script.exists():
        # Can't validate — treat as passed with a note
        return True, 0, 0

    try:
        result = subprocess.run(
            [sys.executable, str(validate_script), str(skill_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        output = result.stdout + result.stderr
        # Verdict: exit 0 = pass (including warnings), exit 1 = fail
        passed = result.returncode == 0
        # Re-parse: validate-skill outputs "❌ ERRORS (N):" and "⚠️  WARNINGS (N):"
        err_m = re.search(r"❌ ERRORS \((\d+)\)", output)
        warn_m = re.search(r"WARNINGS \((\d+)\)", output)
        n_errors = int(err_m.group(1)) if err_m else 0
        n_warnings = int(warn_m.group(1)) if warn_m else 0
        return passed, n_errors, n_warnings
    except Exception:
        return True, 0, 0


# ---------------------------------------------------------------------------
# Metrics logging
# ---------------------------------------------------------------------------

def _ensure_patch_history_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SKILL_PATCH_HISTORY_DDL)


def log_patch_history(
    skill_path: Path,
    old_text: str,
    new_text: str,
    occurrences_replaced: int,
    *,
    replace_all: bool,
    dry_run: bool,
    validation_passed: bool | None,
    validation_errors: int,
    validation_warnings: int,
    db_path: Path | None = None,
) -> bool:
    """
    Write a patch-history record to skill-metrics.db.

    Fail-open: returns False on any error without raising.
    """
    if db_path is None:
        db_path = DEFAULT_METRICS_DB
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        try:
            _ensure_patch_history_schema(conn)
            conn.execute(
                """
                INSERT INTO skill_patch_history (
                    skill_path, patched_at, old_text, new_text,
                    occurrences_replaced, replace_all, dry_run,
                    validation_passed, validation_errors, validation_warnings
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    str(skill_path),
                    datetime.now(timezone.utc).isoformat(),
                    old_text,
                    new_text,
                    occurrences_replaced,
                    1 if replace_all else 0,
                    1 if dry_run else 0,
                    None if validation_passed is None else (1 if validation_passed else 0),
                    validation_errors,
                    validation_warnings,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception as exc:
        print(f"skill-patch: warning: could not log metrics: {exc}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Simple unified-diff helper
# ---------------------------------------------------------------------------

def _simple_diff(before: str, after: str, path: Path) -> str:
    lines_before = before.splitlines(keepends=True)
    lines_after = after.splitlines(keepends=True)
    import difflib
    return "".join(
        difflib.unified_diff(
            lines_before, lines_after,
            fromfile=f"a/{path.name}",
            tofile=f"b/{path.name}",
        )
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="skill-patch",
        description="Targeted patch tool for SKILL.md files.",
        add_help=True,
    )
    parser.add_argument("path", help="Path to SKILL.md or skill directory")
    parser.add_argument("--old", required=True, metavar="TEXT",
                        help="Text to find (fuzzy whitespace matching)")
    parser.add_argument("--new", required=True, metavar="TEXT",
                        help="Replacement text", dest="new_text")
    parser.add_argument("--replace-all", action="store_true",
                        help="Replace all occurrences (default: first only)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show diff; do NOT write or log anything")
    parser.add_argument("--no-validate", action="store_true",
                        help="Skip post-patch validation via validate-skill.py")
    parser.add_argument("--no-metrics", action="store_true",
                        help="Skip logging patch history to skill-metrics.db")
    parser.add_argument("--metrics-db", metavar="PATH",
                        help="Override the skill-metrics.db path")

    args = parser.parse_args(argv)

    # Resolve skill path
    try:
        skill_path = resolve_skill_path(args.path)
    except FileNotFoundError as exc:
        print(f"skill-patch: error: {exc}", file=sys.stderr)
        return 1

    # Load content
    try:
        original = skill_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"skill-patch: error: cannot read {skill_path}: {exc}", file=sys.stderr)
        return 1

    old_text = args.old
    new_text = args.new_text

    # Validate that we have a real old pattern to search for
    try:
        matches = find_occurrences(original, old_text)
    except ValueError as exc:
        print(f"skill-patch: error: {exc}", file=sys.stderr)
        return 2

    if not matches:
        print(
            f"skill-patch: error: pattern not found in {skill_path.name}\n"
            f"  searched for: {old_text!r}\n"
            "  Hint: use --dry-run to preview matches before patching.",
            file=sys.stderr,
        )
        return 1

    count_found = len(matches)
    count_label = "all" if args.replace_all else "first"
    count_will = count_found if args.replace_all else 1

    # Apply patch
    patched, occurrences_replaced = apply_patch(
        original, old_text, new_text, replace_all=args.replace_all
    )

    if occurrences_replaced == 0:
        print("skill-patch: error: pattern matched but replacement yielded 0 substitutions",
              file=sys.stderr)
        return 1

    # Diff display
    diff_text = _simple_diff(original, patched, skill_path)
    if args.dry_run:
        print(f"skill-patch: DRY RUN — {occurrences_replaced}/{count_found} occurrence(s) would be replaced")
        if diff_text:
            print(diff_text, end="")
        else:
            print("(no textual diff detected)")
        return 0

    # Write patched content to a sibling temp file first.
    # The original file is NOT modified until validation has passed.
    try:
        tmp_path = _write_to_temp(skill_path, patched)
    except OSError as exc:
        print(f"skill-patch: error: could not write {skill_path}: {exc}", file=sys.stderr)
        return 1

    print(f"skill-patch: replaced {occurrences_replaced} occurrence(s) ({count_label}) in {skill_path}")
    if diff_text:
        print(diff_text, end="")

    # Validation (runs against the temp file before committing to the final path)
    validation_passed: bool | None = None
    n_errors = 0
    n_warnings = 0

    if not args.no_validate:
        validation_passed, n_errors, n_warnings = run_validation(tmp_path)
        if not validation_passed:
            # Discard the temp file — the original is still intact on disk
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            print(
                f"skill-patch: validation FAILED — {n_errors} error(s), {n_warnings} warning(s)",
                file=sys.stderr,
            )
            return 1
        elif n_warnings:
            print(f"skill-patch: validation OK — {n_warnings} warning(s)")
        else:
            print("skill-patch: validation passed ✓")

    # Commit: atomically replace the original with the validated (or --no-validate) patch
    try:
        os.replace(tmp_path, skill_path)
    except OSError as exc:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        print(f"skill-patch: error: could not commit {skill_path}: {exc}", file=sys.stderr)
        return 1

    # Metrics
    metrics_db: Path | None = None
    if args.metrics_db:
        metrics_db = Path(args.metrics_db).expanduser().resolve()

    if not args.no_metrics:
        log_patch_history(
            skill_path=skill_path,
            old_text=old_text,
            new_text=new_text,
            occurrences_replaced=occurrences_replaced,
            replace_all=args.replace_all,
            dry_run=False,
            validation_passed=validation_passed,
            validation_errors=n_errors,
            validation_warnings=n_warnings,
            db_path=metrics_db,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
