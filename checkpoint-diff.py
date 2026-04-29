#!/usr/bin/env python3
"""
checkpoint-diff.py — Compare checkpoint files for Copilot CLI sessions

Reads checkpoint files written by checkpoint-save.py and shows what changed
between any two checkpoints. All operations are read-only.

Usage:
    python3 checkpoint-diff.py --from N --to M             # Diff checkpoint N vs M
    python3 checkpoint-diff.py --from N --to latest        # Diff N to latest
    python3 checkpoint-diff.py --consecutive               # Diff all consecutive pairs
    python3 checkpoint-diff.py --summary                   # Show change summary across all
    python3 checkpoint-diff.py --session SESSION_ID        # Specify session
    python3 checkpoint-diff.py --session-dir DIR           # Specify session-state root
    python3 checkpoint-diff.py --from N --to M --pager     # Pipe output through pager

Selectors for --from / --to:
    N          Checkpoint sequence number (e.g. 1, 2, 3)
    latest     Most recent checkpoint
    first      Oldest checkpoint

Environment:
    COPILOT_SESSION_ID      Override session detection
    COPILOT_SESSION_STATE   Override session-state root directory
    CHECKPOINT_DIFF_PAGER   Pager command for --pager (default: less -R)
                            Must be one of: less, more, most
"""

import argparse
import difflib
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

if os.name == "nt":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

_env_state = os.environ.get("COPILOT_SESSION_STATE")
SESSION_STATE = Path(_env_state) if _env_state else Path.home() / ".copilot" / "session-state"

# ── Pager helpers ─────────────────────────────────────────────────────────────

_PAGER_ALLOWLIST: frozenset = frozenset({"less", "more", "most"})


def _resolve_pager() -> list[str]:
    """Resolve and validate the pager command.

    Reads CHECKPOINT_DIFF_PAGER env var (default: "less -R").
    Validates the basename is in _PAGER_ALLOWLIST.
    Returns a list of args for subprocess.run(..., shell=False).
    Raises SystemExit on invalid/empty pager.
    """
    raw = os.environ.get("CHECKPOINT_DIFF_PAGER", "less -R")
    args = shlex.split(raw)
    if not args:
        raise SystemExit("Empty pager command in CHECKPOINT_DIFF_PAGER")
    basename = Path(args[0]).name
    if basename not in _PAGER_ALLOWLIST:
        raise SystemExit(
            f"Pager {basename!r} not in allowlist {set(_PAGER_ALLOWLIST)}. "
            "Set CHECKPOINT_DIFF_PAGER to one of: less, more, most"
        )
    return args


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


# ── Diff logic ───────────────────────────────────────────────────────────────


def diff_sections(
    a_sections: dict[str, str],
    b_sections: dict[str, str],
) -> dict[str, dict]:
    """Compute per-section diffs between two checkpoint section dicts.

    Returns a dict mapping section name → {
        "a": original text,
        "b": new text,
        "changed": bool,
        "added": bool,    # present in b but absent in a
        "removed": bool,  # present in a but absent in b
        "lines": [unified diff lines],
    }
    """
    result: dict[str, dict] = {}
    for section in CHECKPOINT_SECTIONS:
        a_text = a_sections.get(section, "")
        b_text = b_sections.get(section, "")
        if a_text == b_text:
            result[section] = {
                "a": a_text,
                "b": b_text,
                "changed": False,
                "added": False,
                "removed": False,
                "lines": [],
            }
            continue
        added = not a_text and bool(b_text)
        removed = bool(a_text) and not b_text
        a_lines = a_text.splitlines(keepends=True)
        b_lines = b_text.splitlines(keepends=True)
        diff_lines = list(
            difflib.unified_diff(
                a_lines,
                b_lines,
                fromfile=f"a/{section}",
                tofile=f"b/{section}",
                lineterm="",
            )
        )
        result[section] = {
            "a": a_text,
            "b": b_text,
            "changed": True,
            "added": added,
            "removed": removed,
            "lines": diff_lines,
        }
    return result


def format_diff_output(
    entry_a: dict,
    entry_b: dict,
    section_diffs: dict[str, dict],
    *,
    show_unchanged: bool = False,
) -> str:
    """Format diff output for human display."""
    lines = [
        f"─── diff [{entry_a['seq']:03d}] {entry_a['title']}",
        f"       vs [{entry_b['seq']:03d}] {entry_b['title']}",
        "",
    ]
    any_change = False
    for section in CHECKPOINT_SECTIONS:
        d = section_diffs.get(section)
        if d is None:
            continue
        if not d["changed"]:
            if show_unchanged and (d["a"] or d["b"]):
                lines.append(f"  (unchanged) [{section}]")
            continue
        any_change = True
        label = section.replace("_", " ").upper()
        if d["added"]:
            status = "ADDED"
        elif d["removed"]:
            status = "REMOVED"
        else:
            status = "CHANGED"
        lines.append(f"  [{status}] {label}")
        for dl in d["lines"]:
            lines.append(f"    {dl}")
        lines.append("")
    if not any_change:
        lines.append("  (no section-level changes detected)")
    return "\n".join(lines)


def format_summary_output(entries: list[dict], all_diffs: list[dict]) -> str:
    """Format a multi-pair summary table showing the checkpoint progression."""
    lines = [f"Checkpoint progression summary ({len(entries)} checkpoints):"]
    for i, entry in enumerate(entries):
        marker = "●" if i == 0 else "↓"
        lines.append(f"  {marker} [{entry['seq']:03d}] {entry['title']}")
        if i < len(all_diffs):
            changed = [s for s, v in all_diffs[i].items() if v["changed"]]
            if changed:
                lines.append(f"        ↳ changed: {', '.join(changed)}")
    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Compare checkpoint files (read-only)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--from", dest="from_sel", metavar="SELECTOR", help="Base checkpoint ('first', 'latest', or seq number)"
    )
    p.add_argument(
        "--to", dest="to_sel", metavar="SELECTOR", help="Target checkpoint ('first', 'latest', or seq number)"
    )
    p.add_argument("--consecutive", action="store_true", help="Diff all consecutive checkpoint pairs")
    p.add_argument("--summary", action="store_true", help="Show progression summary across all checkpoints")
    p.add_argument("--show-unchanged", action="store_true", help="Include unchanged sections in diff output")
    p.add_argument("--session", metavar="SESSION_ID", default=None, help="Specific session ID")
    p.add_argument("--session-dir", metavar="DIR", default=None, help="Session-state root directory")
    p.add_argument("--pager", action="store_true", help="Pipe output through pager (see CHECKPOINT_DIFF_PAGER env var)")
    color_group = p.add_mutually_exclusive_group()
    color_group.add_argument("--color", dest="color", action="store_true", default=None, help="Force ANSI color output")
    color_group.add_argument(
        "--no-color", dest="color", action="store_false", help="Disable ANSI color output (default: auto-detect TTY)"
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not any([args.from_sel, args.consecutive, args.summary]):
        parser.print_help()
        return 0

    # Color: explicit flag > auto-detect TTY
    use_color: bool
    if args.color is True:
        use_color = True
    elif args.color is False:
        use_color = False
    else:
        use_color = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

    state_root = Path(args.session_dir) if args.session_dir else SESSION_STATE

    session_dir = detect_session(state_root, args.session)
    if session_dir is None:
        print("✗ Could not find a session directory.", file=sys.stderr)
        print(f"  Searched in: {state_root}", file=sys.stderr)
        print("  Use --session SESSION_ID to specify one explicitly.", file=sys.stderr)
        return 1

    index_path = session_dir / "checkpoints" / "index.md"
    entries = parse_index(index_path)

    if not entries:
        print(f"✗ No checkpoints in session: {session_dir.name}", file=sys.stderr)
        return 1

    cp_dir = session_dir / "checkpoints"
    output_lines: list[str] = []

    # --summary
    if args.summary:
        sorted_entries = sorted(entries, key=lambda e: e["seq"])
        all_diffs = []
        for i in range(len(sorted_entries) - 1):
            ea = sorted_entries[i]
            eb = sorted_entries[i + 1]
            sa = parse_checkpoint_sections(cp_dir / ea["file"])
            sb = parse_checkpoint_sections(cp_dir / eb["file"])
            all_diffs.append(diff_sections(sa, sb))
        output_lines.append(format_summary_output(sorted_entries, all_diffs))

    # --consecutive
    elif args.consecutive:
        sorted_entries = sorted(entries, key=lambda e: e["seq"])
        if len(sorted_entries) < 2:
            print("✗ Need at least 2 checkpoints to diff.", file=sys.stderr)
            return 1
        for i in range(len(sorted_entries) - 1):
            ea = sorted_entries[i]
            eb = sorted_entries[i + 1]
            sa = parse_checkpoint_sections(cp_dir / ea["file"])
            sb = parse_checkpoint_sections(cp_dir / eb["file"])
            diffs = diff_sections(sa, sb)
            output_lines.append(format_diff_output(ea, eb, diffs, show_unchanged=args.show_unchanged))
            output_lines.append("")

    # --from / --to
    else:
        if not args.to_sel:
            print("✗ --to is required when using --from.", file=sys.stderr)
            return 1

        entry_a = resolve_selector(entries, args.from_sel)
        if entry_a is None:
            available = ", ".join(str(e["seq"]) for e in sorted(entries, key=lambda e: e["seq"]))
            print(f"✗ Checkpoint '{args.from_sel}' not found. Available: {available}", file=sys.stderr)
            return 1

        entry_b = resolve_selector(entries, args.to_sel)
        if entry_b is None:
            available = ", ".join(str(e["seq"]) for e in sorted(entries, key=lambda e: e["seq"]))
            print(f"✗ Checkpoint '{args.to_sel}' not found. Available: {available}", file=sys.stderr)
            return 1

        if entry_a["seq"] == entry_b["seq"]:
            print("✗ Cannot diff a checkpoint against itself.", file=sys.stderr)
            return 1

        sa = parse_checkpoint_sections(cp_dir / entry_a["file"])
        sb = parse_checkpoint_sections(cp_dir / entry_b["file"])
        diffs = diff_sections(sa, sb)
        output_lines.append(format_diff_output(entry_a, entry_b, diffs, show_unchanged=args.show_unchanged))

    diff_text = "\n".join(output_lines)

    if args.pager:
        pager_args = _resolve_pager()
        subprocess.run(pager_args, shell=False, input=diff_text, text=True)
    else:
        print(diff_text)

    return 0


if __name__ == "__main__":
    sys.exit(main())
