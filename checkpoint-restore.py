#!/usr/bin/env python3
"""
checkpoint-restore.py — Inspect and display checkpoint files for Copilot CLI sessions

Reads checkpoint files written by checkpoint-save.py. All operations are read-only;
no session state is mutated.

Usage:
    python3 checkpoint-restore.py --list                        # List all checkpoints
    python3 checkpoint-restore.py --show SELECTOR               # Show a checkpoint
    python3 checkpoint-restore.py --export SELECTOR [--format FORMAT]
    python3 checkpoint-restore.py --session SESSION_ID          # Specify session
    python3 checkpoint-restore.py --session-dir DIR             # Specify session-state root

Selectors for --show / --export:
    N          Checkpoint sequence number (e.g. 1, 2, 3)
    latest     Most recent checkpoint
    first      Oldest checkpoint

Formats for --export:
    text       Human-readable text (default)
    md         Markdown with XML section tags (indexer-compatible)
    json       Machine-readable JSON

Environment:
    COPILOT_SESSION_ID     Override session detection
    COPILOT_SESSION_STATE  Override session-state root directory
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

if os.name == "nt":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

_env_state = os.environ.get("COPILOT_SESSION_STATE")
SESSION_STATE = Path(_env_state) if _env_state else Path.home() / ".copilot" / "session-state"

CHECKPOINT_SECTIONS = [
    "overview",
    "history",
    "work_done",
    "technical_details",
    "important_files",
    "next_steps",
]


# ── Session detection (mirrors checkpoint-save.py exactly) ───────────────────


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

        for line in text.splitlines():
            if line.startswith("cwd:"):
                line_cwd = line[4:].strip()
                if line_cwd == cwd:
                    if mtime > best_cwd_mtime:
                        best_cwd_mtime = mtime
                        best_cwd = d
                break

    return best_cwd or best_any


# ── Index helpers (mirrors checkpoint-save.py exactly) ───────────────────────


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


# ── Checkpoint parsing ───────────────────────────────────────────────────────


def parse_checkpoint_sections(cp_path: Path) -> dict[str, str]:
    """Parse XML-tagged sections from a checkpoint file.

    Returns a dict mapping section name → content string.
    Sections absent from the file are omitted from the result.
    Missing or unreadable files return an empty dict.

    Uses a single-pass position-aware scanner so that literal tag text
    embedded inside one section's content cannot bleed into another
    section's match.
    """
    if not cp_path.exists():
        return {}
    try:
        text = cp_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {}

    sections: dict[str, str] = {}
    all_names = "|".join(re.escape(s) for s in CHECKPOINT_SECTIONS)
    tag_re = re.compile(rf"<(/?)({all_names})>")

    current: str | None = None
    content_start: int = 0
    for m in tag_re.finditer(text):
        is_close = m.group(1) == "/"
        name = m.group(2)
        if not is_close:
            if current is None:
                current = name
                content_start = m.end()
        else:
            if current == name:
                sections[name] = text[content_start : m.start()].strip()
                current = None
    return sections


# ── Selector resolution ──────────────────────────────────────────────────────


def resolve_selector(entries: list[dict], selector: str) -> dict | None:
    """Resolve a checkpoint selector to an index entry dict.

    Accepted selectors:
        'latest'   — highest seq number
        'first'    — lowest seq number
        N (int)    — exact seq match

    Returns None if nothing matches or entries is empty.
    """
    if not entries:
        return None
    sel = selector.strip().lower()
    if sel == "latest":
        return max(entries, key=lambda e: e["seq"])
    if sel == "first":
        return min(entries, key=lambda e: e["seq"])
    try:
        n = int(sel)
    except ValueError:
        return None
    for e in entries:
        if e["seq"] == n:
            return e
    return None


# ── Output formatters ────────────────────────────────────────────────────────


def format_checkpoint_text(entry: dict, sections: dict[str, str]) -> str:
    """Format a checkpoint as human-readable text."""
    lines = [
        f"# [{entry['seq']:03d}] {entry['title']}",
        f"File: {entry['file']}",
        "",
    ]
    for section in CHECKPOINT_SECTIONS:
        content = sections.get(section, "")
        if content:
            heading = section.replace("_", " ").title()
            lines.append(f"## {heading}")
            lines.append(content)
            lines.append("")
    return "\n".join(lines)


def format_checkpoint_md(entry: dict, sections: dict[str, str]) -> str:
    """Format a checkpoint as markdown with XML section tags (indexer-compatible)."""
    lines = [f"# [{entry['seq']:03d}] {entry['title']}", ""]
    for section in CHECKPOINT_SECTIONS:
        content = sections.get(section, "")
        if content:
            lines.append(f"<{section}>")
            lines.append(content)
            lines.append(f"</{section}>")
            lines.append("")
    return "\n".join(lines)


def format_checkpoint_json(entry: dict, sections: dict[str, str]) -> str:
    """Format a checkpoint as JSON."""
    data = {
        "seq": entry["seq"],
        "title": entry["title"],
        "file": entry["file"],
        "sections": sections,
    }
    return json.dumps(data, indent=2, ensure_ascii=False)


# ── CLI ──────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Inspect and display checkpoint files (read-only)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--list", action="store_true", help="List all checkpoints for the session")
    p.add_argument("--show", metavar="SELECTOR", help="Show a checkpoint ('latest', 'first', or seq number)")
    p.add_argument("--export", metavar="SELECTOR", help="Export a checkpoint in the chosen format")
    p.add_argument(
        "--format", choices=["text", "md", "json"], default="text", help="Output format for --export (default: text)"
    )
    p.add_argument("--session", metavar="SESSION_ID", default=None, help="Specific session ID")
    p.add_argument("--session-dir", metavar="DIR", default=None, help="Session-state root directory")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not any([args.list, args.show, args.export]):
        parser.print_help()
        return 0

    state_root = Path(args.session_dir) if args.session_dir else SESSION_STATE

    session_dir = detect_session(state_root, args.session)
    if session_dir is None:
        print("✗ Could not find a session directory.", file=sys.stderr)
        print(f"  Searched in: {state_root}", file=sys.stderr)
        print("  Use --session SESSION_ID to specify one explicitly.", file=sys.stderr)
        return 1

    index_path = session_dir / "checkpoints" / "index.md"
    entries = parse_index(index_path)

    # --list
    if args.list:
        if not entries:
            print(f"No checkpoints in session: {session_dir.name}")
        else:
            print(f"Checkpoints for session {session_dir.name}:")
            for e in entries:
                print(f"  [{e['seq']:3d}] {e['title']}  ({e['file']})")
        return 0

    # --show / --export
    selector = args.show if args.show is not None else args.export
    entry = resolve_selector(entries, selector)
    if entry is None:
        if not entries:
            print(f"✗ No checkpoints in session: {session_dir.name}", file=sys.stderr)
        else:
            available = ", ".join(str(e["seq"]) for e in sorted(entries, key=lambda e: e["seq"]))
            print(
                f"✗ Checkpoint '{selector}' not found. Available: {available}",
                file=sys.stderr,
            )
        return 1

    cp_path = session_dir / "checkpoints" / entry["file"]
    sections = parse_checkpoint_sections(cp_path)

    if args.show is not None:
        print(format_checkpoint_text(entry, sections))
        return 0

    # --export
    fmt = args.format
    if fmt == "json":
        print(format_checkpoint_json(entry, sections))
    elif fmt == "md":
        print(format_checkpoint_md(entry, sections))
    else:
        print(format_checkpoint_text(entry, sections))
    return 0


if __name__ == "__main__":
    sys.exit(main())
