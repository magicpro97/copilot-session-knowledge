#!/usr/bin/env python3
"""
checkpoint-save.py — Write checkpoint files for Copilot CLI sessions

Creates indexed checkpoint files under ~/.copilot/session-state/<session>/checkpoints/
in the exact format consumed by build-session-index.py.

Usage:
    python3 checkpoint-save.py --title "Checkpoint title" [options]
    python3 checkpoint-save.py --title "Title" --overview "..." --next_steps "..."
    python3 checkpoint-save.py --list                   # List checkpoints in session
    python3 checkpoint-save.py --session SESSION_ID     # Override session detection

Options:
    --title TEXT           Checkpoint title (required unless --list)
    --overview TEXT        Overview / summary of what was worked on
    --history TEXT         Chronological history of events
    --work_done TEXT       Work completed (files created/modified)
    --technical_details TEXT  Technical context, patterns, blockers
    --important_files TEXT    Key files and their roles
    --next_steps TEXT      What to do next / blockers
    --session SESSION_ID   Use a specific session ID
    --session-dir DIR      Use a specific session-state directory
    --list                 List all checkpoints for the current session
    --dry-run              Print what would be written, do not write

Environment:
    COPILOT_SESSION_ID     Session ID to use (overrides auto-detection)
    COPILOT_SESSION_STATE  Override session-state directory path
"""

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

if os.name == "nt":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

_env_state = os.environ.get("COPILOT_SESSION_STATE")
SESSION_STATE = Path(_env_state) if _env_state else Path.home() / ".copilot" / "session-state"

CHECKPOINT_SECTIONS = ["overview", "history", "work_done", "technical_details", "important_files", "next_steps"]

INDEX_HEADER = """\
# Checkpoint History

Checkpoints are listed in chronological order. Checkpoint 1 is the oldest, higher numbers are more recent.

| # | Title | File |
|---|-------|------|
"""


# ── Session detection ───────────────────────────────────────────────────────


def detect_session(session_state_dir: Path, session_id: str | None = None) -> Path | None:
    """Return the session directory to use.

    Priority:
      1. Explicit session_id argument
      2. COPILOT_SESSION_ID environment variable
      3. Most recently updated session whose workspace.yaml cwd matches cwd
      4. Most recently updated session overall
    """
    if session_id:
        candidate = session_state_dir / session_id
        if candidate.is_dir():
            return candidate
        print(f"✗ Session not found: {session_id}", file=sys.stderr)
        return None

    env_sid = os.environ.get("COPILOT_SESSION_ID")
    if env_sid:
        candidate = session_state_dir / env_sid
        if candidate.is_dir():
            return candidate

    if not session_state_dir.is_dir():
        return None

    cwd = str(Path.cwd())
    best_cwd: Path | None = None
    best_cwd_mtime: float = 0.0
    best_any: Path | None = None
    best_any_mtime: float = 0.0

    for d in session_state_dir.iterdir():
        if not d.is_dir():
            continue
        yaml_path = d / "workspace.yaml"
        if not yaml_path.exists():
            continue
        try:
            mtime = yaml_path.stat().st_mtime
            text = yaml_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        if mtime > best_any_mtime:
            best_any_mtime = mtime
            best_any = d

        # Check if this session's cwd matches (exact comparison, not substring)
        for line in text.splitlines():
            if line.startswith("cwd:"):
                line_cwd = line[4:].strip()
                if line_cwd == cwd:
                    if mtime > best_cwd_mtime:
                        best_cwd_mtime = mtime
                        best_cwd = d
                break

    return best_cwd or best_any


# ── Index helpers ────────────────────────────────────────────────────────────


def parse_index(index_path: Path) -> list[dict]:
    """Parse checkpoints/index.md into a list of {seq, title, file} dicts."""
    if not index_path.exists():
        return []
    entries = []
    for line in index_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r"\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|", line)
        if m:
            entries.append(
                {
                    "seq": int(m.group(1)),
                    "title": m.group(2).strip(),
                    "file": m.group(3).strip(),
                }
            )
    return entries


def write_index(index_path: Path, entries: list[dict]) -> None:
    """Overwrite checkpoints/index.md with the given entries."""
    lines = [INDEX_HEADER]
    for e in entries:
        lines.append(f"| {e['seq']} | {e['title']} | {e['file']} |")
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── Filename helpers ─────────────────────────────────────────────────────────


def slug(title: str, max_len: int = 35) -> str:
    """Convert a title to a URL-safe slug."""
    s = title.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s[:max_len].rstrip("-")


def next_filename(entries: list[dict], title: str) -> tuple[int, str]:
    """Return (seq, filename) for a new checkpoint."""
    seq = (max((e["seq"] for e in entries), default=0)) + 1
    fname = f"{seq:03d}-{slug(title)}.md"
    return seq, fname


# ── Checkpoint writing ───────────────────────────────────────────────────────


def build_checkpoint_content(title: str, sections: dict[str, str]) -> str:
    """Build the checkpoint markdown content with XML section tags."""
    parts: list[str] = []
    for section in CHECKPOINT_SECTIONS:
        content = sections.get(section, "").strip()
        if content:
            parts.append(f"<{section}>\n{content}\n</{section}>")
    # Always include overview if missing so the file has at least one section
    if not parts:
        parts.append(f"<overview>\n{title}\n</overview>")
    return "\n\n".join(parts) + "\n"


def save_checkpoint(
    session_dir: Path,
    title: str,
    sections: dict[str, str],
    dry_run: bool = False,
) -> Path:
    """Write checkpoint file and update index. Returns the checkpoint path."""
    cp_dir = session_dir / "checkpoints"
    index_path = cp_dir / "index.md"

    entries = parse_index(index_path)
    seq, fname = next_filename(entries, title)
    cp_path = cp_dir / fname
    content = build_checkpoint_content(title, sections)

    if dry_run:
        print(f"[dry-run] Would write: {cp_path}")
        print(f"[dry-run] Would add index entry: | {seq} | {title} | {fname} |")
        print("─" * 60)
        print(content)
        return cp_path

    cp_dir.mkdir(parents=True, exist_ok=True)

    # Write checkpoint file
    cp_path.write_text(content, encoding="utf-8")

    # Update index
    entries.append({"seq": seq, "title": title, "file": fname})
    write_index(index_path, entries)

    return cp_path


# ── CLI ──────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Write checkpoint files for Copilot CLI sessions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--title", help="Checkpoint title (required unless --list)")
    p.add_argument("--overview", default="", help="Overview / what was worked on")
    p.add_argument("--history", default="", help="Chronological history of events")
    p.add_argument("--work_done", default="", help="Work completed (files created/modified)")
    p.add_argument("--technical_details", default="", help="Technical context, patterns, blockers")
    p.add_argument("--important_files", default="", help="Key files and their roles")
    p.add_argument("--next_steps", default="", help="What to do next / blockers")
    p.add_argument("--session", metavar="SESSION_ID", default=None, help="Specific session ID")
    p.add_argument("--session-dir", metavar="DIR", default=None, help="Session-state root directory")
    p.add_argument("--list", action="store_true", help="List checkpoints for the current session")
    p.add_argument("--dry-run", action="store_true", help="Print output without writing files")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Determine session-state root
    state_root = Path(args.session_dir) if args.session_dir else SESSION_STATE

    # Detect session
    session_dir = detect_session(state_root, args.session)
    if session_dir is None:
        print("✗ Could not find a session directory.", file=sys.stderr)
        print(f"  Searched in: {state_root}", file=sys.stderr)
        print("  Use --session SESSION_ID to specify one explicitly.", file=sys.stderr)
        return 1

    # --list mode
    if args.list:
        index_path = session_dir / "checkpoints" / "index.md"
        entries = parse_index(index_path)
        if not entries:
            print(f"No checkpoints in session: {session_dir.name}")
        else:
            print(f"Checkpoints for session {session_dir.name}:")
            for e in entries:
                print(f"  [{e['seq']:3d}] {e['title']}  ({e['file']})")
        return 0

    # Require --title for save
    if not args.title:
        parser.error("--title is required when not using --list")

    # Validate title length
    title = args.title.strip()
    if len(title) > 200:
        print("✗ Title too long (max 200 characters)", file=sys.stderr)
        return 1

    sections = {
        "overview": args.overview,
        "history": args.history,
        "work_done": args.work_done,
        "technical_details": args.technical_details,
        "important_files": args.important_files,
        "next_steps": args.next_steps,
    }

    cp_path = save_checkpoint(session_dir, title, sections, dry_run=args.dry_run)

    if not args.dry_run:
        print(f"✓ Checkpoint saved: {cp_path}")
        print(f"  Session: {session_dir.name}")
        # Suggest re-indexing
        print("  Run: python3 ~/.copilot/tools/build-session-index.py --incremental")

    return 0


if __name__ == "__main__":
    sys.exit(main())
