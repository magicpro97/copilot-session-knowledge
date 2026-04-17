#!/usr/bin/env python3
"""track-bash-edits.py — postToolUse hook (cross-platform)

After ANY bash command, run `git status` to detect actual file modifications.
Updates edit counters used by enforce-learn gate. This is language-agnostic —
catches python, node, ruby, cp, mv, tee, curl, or ANY method of writing files.

Strategy: Pattern matching can never catch all bash file writes.
Instead, check what ACTUALLY changed on disk after the command ran.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

if os.name == "nt":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

MARKERS_DIR = Path.home() / ".copilot" / "markers"
CODE_EDIT_COUNTER = MARKERS_DIR / "code-edit-count"
PY_EDIT_COUNTER = MARKERS_DIR / "py-edit-count"
SEEN_MODIFIED = MARKERS_DIR / "git-modified-seen"
TENTACLE_EDITS = MARKERS_DIR / "tentacle-edits"

CODE_EXTENSIONS = {".py", ".kt", ".ts", ".tsx", ".js", ".jsx", ".swift", ".java",
                   ".go", ".rs", ".json", ".yaml", ".yml", ".xml", ".html",
                   ".css", ".toml", ".md", ".sh", ".bat", ".ps1"}


def _get_git_modified():
    """Get list of modified/added files from git status."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "-uall"],
            capture_output=True, text=True, timeout=5, cwd=os.getcwd()
        )
        if result.returncode != 0:
            return set()

        files = set()
        for line in result.stdout.strip().splitlines():
            if not line or len(line) < 4:
                continue
            status = line[:2]
            filepath = line[3:].strip()
            # Skip deleted files
            if status.strip().startswith("D"):
                continue
            # Handle renamed files (old -> new)
            if " -> " in filepath:
                filepath = filepath.split(" -> ")[-1]
            files.add(filepath)
        return files
    except Exception:
        return set()


def _load_seen():
    """Load previously seen modified files."""
    try:
        if SEEN_MODIFIED.is_file():
            return set(SEEN_MODIFIED.read_text().strip().splitlines())
    except Exception:
        pass
    return set()


def _save_seen(seen):
    """Save seen modified files."""
    try:
        MARKERS_DIR.mkdir(parents=True, exist_ok=True)
        SEEN_MODIFIED.write_text("\n".join(sorted(seen)))
    except Exception:
        pass


def _increment_counter(counter_path, amount):
    """Increment a counter marker file."""
    if amount <= 0:
        return
    try:
        MARKERS_DIR.mkdir(parents=True, exist_ok=True)
        count = 0
        if counter_path.is_file():
            count = int(counter_path.read_text().strip())
        count += amount
        counter_path.write_text(str(count))
    except Exception:
        pass


def _update_tentacle_tracker(new_files):
    """Update tentacle edit tracker with new files."""
    if not new_files:
        return
    try:
        MARKERS_DIR.mkdir(parents=True, exist_ok=True)
        existing = set()
        if TENTACLE_EDITS.is_file():
            existing = set(TENTACLE_EDITS.read_text().strip().splitlines())
        existing.update(new_files)
        TENTACLE_EDITS.write_text("\n".join(sorted(existing)))
    except Exception:
        pass


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        return

    tool_name = data.get("toolName", "")
    if tool_name != "bash":
        return

    # Get currently modified files from git
    current_modified = _get_git_modified()
    if not current_modified:
        return

    # Compare with previously seen
    previously_seen = _load_seen()
    new_modifications = current_modified - previously_seen

    if not new_modifications:
        return

    # Filter for code files
    new_code_files = set()
    new_py_files = set()
    for f in new_modifications:
        suffix = Path(f).suffix.lower()
        if suffix in CODE_EXTENSIONS:
            new_code_files.add(f)
        if suffix == ".py":
            new_py_files.add(f)

    # Update counters
    if new_code_files:
        _increment_counter(CODE_EDIT_COUNTER, len(new_code_files))
        _update_tentacle_tracker(new_code_files)

    if new_py_files:
        _increment_counter(PY_EDIT_COUNTER, len(new_py_files))

    # Save updated seen set
    _save_seen(previously_seen | current_modified)

    # Log for visibility (agent sees this)
    if new_code_files:
        print(f"  📝 Detected {len(new_code_files)} file change(s) via bash: {', '.join(sorted(new_code_files)[:5])}")
        if len(new_code_files) > 5:
            print(f"     ... and {len(new_code_files) - 5} more")


if __name__ == "__main__":
    main()
