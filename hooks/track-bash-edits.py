#!/usr/bin/env python3
"""track-bash-edits.py — postToolUse hook (cross-platform)

After ANY bash command, run git status to detect file modifications.
Updates HMAC-signed counters and list markers.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from marker_auth import sign_counter, sign_list_marker, verify_counter, verify_list_marker
except ImportError:

    def sign_counter(p, v):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(v), encoding="utf-8")

    def verify_counter(p):
        try:
            return int(p.read_text(encoding="utf-8").strip()) if p.is_file() else 0
        except Exception:
            return 0

    def sign_list_marker(p, l):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(sorted(l)))

    def verify_list_marker(p):
        try:
            return set(p.read_text(encoding="utf-8").strip().splitlines()) if p.is_file() else set()
        except Exception:
            return set()


MARKERS_DIR = Path.home() / ".copilot" / "markers"
CODE_EDIT_COUNTER = MARKERS_DIR / "code-edit-count"
PY_EDIT_COUNTER = MARKERS_DIR / "py-edit-count"
SEEN_MODIFIED = MARKERS_DIR / "git-modified-seen"
TENTACLE_EDITS = MARKERS_DIR / "tentacle-edits"

# Markdown (.md) intentionally excluded: session-research writes must not count
# as multi-module code edits and trigger false tentacle enforcement.
CODE_EXTENSIONS = {
    ".py",
    ".kt",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".swift",
    ".java",
    ".go",
    ".rs",
    ".json",
    ".yaml",
    ".yml",
    ".xml",
    ".html",
    ".css",
    ".toml",
    ".sh",
    ".bat",
    ".ps1",
}

_SESSION_STATE_ABS = str(Path.home() / ".copilot" / "session-state")


def _is_session_path(path: str) -> bool:
    """Return True if path is under ~/.copilot/session-state/."""
    return path.startswith(_SESSION_STATE_ABS) or ".copilot/session-state" in path


def _get_git_modified():
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "-uall"], capture_output=True, text=True, timeout=5, cwd=os.getcwd()
        )
        if result.returncode != 0:
            return set()
        files = set()
        for line in result.stdout.strip().splitlines():
            if not line or len(line) < 4:
                continue
            status = line[:2]
            filepath = line[3:].strip()
            if status.strip().startswith("D"):
                continue
            if " -> " in filepath:
                filepath = filepath.split(" -> ")[-1]
            files.add(filepath)
        return files
    except Exception:
        return set()


def _load_seen():
    # git-modified-seen is internal tracking — plain text OK
    try:
        if SEEN_MODIFIED.is_file():
            return set(SEEN_MODIFIED.read_text(encoding="utf-8").strip().splitlines())
    except Exception:
        pass
    return set()


def _save_seen(seen):
    try:
        MARKERS_DIR.mkdir(parents=True, exist_ok=True)
        SEEN_MODIFIED.write_text("\n".join(sorted(seen)))
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

    current_modified = _get_git_modified()
    if not current_modified:
        return

    previously_seen = _load_seen()
    new_modifications = current_modified - previously_seen

    if not new_modifications:
        return

    new_code_files = set()
    new_py_files = set()
    for f in new_modifications:
        suffix = Path(f).suffix.lower()
        if suffix in CODE_EXTENSIONS and not _is_session_path(f):
            new_code_files.add(f)
        if suffix == ".py":
            new_py_files.add(f)

    # Update signed counters
    if new_code_files:
        current_count = verify_counter(CODE_EDIT_COUNTER)
        sign_counter(CODE_EDIT_COUNTER, current_count + len(new_code_files))
        # Update signed tentacle edit tracker
        existing = verify_list_marker(TENTACLE_EDITS)
        existing.update(new_code_files)
        sign_list_marker(TENTACLE_EDITS, existing)

    if new_py_files:
        py_count = verify_counter(PY_EDIT_COUNTER)
        sign_counter(PY_EDIT_COUNTER, py_count + len(new_py_files))

    _save_seen(previously_seen | current_modified)

    if new_code_files:
        print(f"  📝 Detected {len(new_code_files)} file change(s) via bash: {', '.join(sorted(new_code_files)[:5])}")
        if len(new_code_files) > 5:
            print(f"     ... and {len(new_code_files) - 5} more")


if __name__ == "__main__":
    main()
