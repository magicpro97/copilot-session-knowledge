#!/usr/bin/env python3
"""
providers/copilot_provider.py — CopilotProvider implementation.

Reads Copilot CLI session-state directories (UUID-named dirs with
checkpoints/, research/, files/, plan.md).

Pure stdlib. Python 3.10+.
"""

import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .base import (
    Event,
    EventKind,
    MAX_CONTENT_CHARS,
    SessionMeta,
    SessionProvider,
)

# Fix Windows console encoding — mandatory pattern in this repo.
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────

_COPILOT_DEFAULT_ROOT: Path = Path.home() / ".copilot" / "session-state"
_COPILOT_SESSION_RE: re.Pattern = re.compile(r"^[0-9a-f]{8}-")
MIN_SESSION_BYTES: int = 1_024

_CHECKPOINT_SECTIONS: tuple[str, ...] = (
    "overview", "history", "work_done", "technical_details",
    "important_files", "next_steps",
)

# Maps checkpoint section name → EventKind
_SECTION_KINDS: dict[str, str] = {
    "overview": "note",
    "history": "note",
    "work_done": "assistant_msg",
    "technical_details": "assistant_msg",
    "important_files": "diff",
    "next_steps": "note",
}


# ──────────────────────────────────────────────
# CopilotProvider
# ──────────────────────────────────────────────

class CopilotProvider(SessionProvider):
    """Provider for GitHub Copilot CLI session-state directories.

    Session structure (mirrors build-session-index.py):
        ~/.copilot/session-state/
          <uuid>/
            checkpoints/index.md
            checkpoints/<seq>-<slug>.md
            research/*.md
            files/*.md, *.txt
            plan.md
    """

    name: str = "copilot"

    def _discover_root(self) -> Path | None:
        """Return Copilot session-state root, respecting COPILOT_SESSION_STATE env."""
        override = os.environ.get("COPILOT_SESSION_STATE", "").strip()
        root = Path(override) if override else _COPILOT_DEFAULT_ROOT
        return root if root.exists() else None

    def list_sessions(self) -> Iterator[SessionMeta]:
        """Yield SessionMeta for each UUID-named directory under the Copilot root.

        Only directories matching _COPILOT_SESSION_RE are considered sessions,
        mirroring build-session-index.py lines 432–434.
        """
        root = self._discover_root()
        if root is None:
            return

        for session_dir in sorted(root.iterdir()):
            if not session_dir.is_dir():
                continue
            if not _COPILOT_SESSION_RE.match(session_dir.name):
                continue

            try:
                all_files = [f for f in session_dir.rglob("*") if f.is_file()]
            except OSError:
                continue

            total_size = sum(f.stat().st_size for f in all_files)
            if total_size < MIN_SESSION_BYTES:
                continue

            mtime = max(
                (f.stat().st_mtime for f in all_files),
                default=0.0,
            )

            # Attempt to extract title from plan.md first line
            title = _read_title(session_dir)

            yield SessionMeta(
                id=session_dir.name,
                provider=self.name,
                path=session_dir,
                title=title,
                mtime=mtime,
            )

    def iter_events(
        self, session: SessionMeta, *, from_event: int = 0
    ) -> Iterator[Event]:
        """Yield Events from a Copilot session directory.

        Emission order:
          1. plan.md → kind="system"
          2. checkpoints (index.md order) → one event per non-empty section
          3. research/*.md → kind="assistant_msg"
          4. files/*.md, files/*.txt → kind="note"

        from_event: skip the first N events (sequential skip).
        """
        event_counter = 0
        session_dir = session.path

        def _maybe_yield(event: Event) -> Iterator[Event]:
            nonlocal event_counter
            if event_counter >= from_event:
                yield event
            event_counter += 1

        # 1. plan.md → system
        plan_path = session_dir / "plan.md"
        if plan_path.exists() and plan_path.stat().st_size > 50:
            content = _safe_read(plan_path)
            if content:
                yield from _maybe_yield(Event(
                    session_id=session.id,
                    event_id=event_counter,
                    ts=None,
                    kind="system",
                    content=content[:MAX_CONTENT_CHARS],
                    raw_ref=f"{session.id}/plan.md",
                ))

        # 2. Checkpoints
        index_path = session_dir / "checkpoints" / "index.md"
        for cp_entry in _parse_checkpoint_index(index_path):
            cp_path = session_dir / "checkpoints" / cp_entry["file"]
            if not cp_path.exists():
                continue
            cp_content = _safe_read(cp_path)
            for section_name in _CHECKPOINT_SECTIONS:
                section_text = _extract_xml_section(cp_content, section_name)
                if not section_text:
                    continue
                kind: str = _SECTION_KINDS.get(section_name, "note")
                yield from _maybe_yield(Event(
                    session_id=session.id,
                    event_id=event_counter,
                    ts=None,
                    kind=kind,
                    content=section_text[:MAX_CONTENT_CHARS],
                    raw_ref=f"{session.id}/checkpoints/{cp_entry['file']}:<{section_name}>",
                ))

        # 3. Research docs
        research_dir = session_dir / "research"
        if research_dir.exists():
            for md_file in sorted(research_dir.glob("*.md")):
                content = _safe_read(md_file)
                if content:
                    yield from _maybe_yield(Event(
                        session_id=session.id,
                        event_id=event_counter,
                        ts=None,
                        kind="assistant_msg",
                        content=content[:MAX_CONTENT_CHARS],
                        raw_ref=f"{session.id}/research/{md_file.name}",
                    ))

        # 4. Files/artifacts
        files_dir = session_dir / "files"
        if files_dir.exists():
            for art_file in sorted(files_dir.iterdir()):
                if art_file.is_file() and art_file.suffix in (".md", ".txt"):
                    content = _safe_read(art_file)
                    if content:
                        yield from _maybe_yield(Event(
                            session_id=session.id,
                            event_id=event_counter,
                            ts=None,
                            kind="note",
                            content=content[:MAX_CONTENT_CHARS],
                            raw_ref=f"{session.id}/files/{art_file.name}",
                        ))


# ──────────────────────────────────────────────
# Private helpers
# ──────────────────────────────────────────────

def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _parse_checkpoint_index(index_path: Path) -> list[dict]:
    """Parse checkpoints/index.md → list of {seq, title, file}."""
    if not index_path.exists():
        return []
    entries = []
    for line in _safe_read(index_path).splitlines():
        m = re.match(r"\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|", line)
        if m:
            entries.append({
                "seq": int(m.group(1)),
                "title": m.group(2).strip(),
                "file": m.group(3).strip(),
            })
    return entries


def _extract_xml_section(content: str, tag: str) -> str:
    """Extract XML-tagged section from checkpoint markdown."""
    m = re.search(f"<{tag}>(.*?)</{tag}>", content, re.DOTALL)
    return m.group(1).strip() if m else ""


def _read_title(session_dir: Path) -> str | None:
    """Try to read a short title from plan.md first non-empty line."""
    plan = session_dir / "plan.md"
    if not plan.exists():
        return None
    try:
        for line in plan.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip().lstrip("#").strip()
            if line:
                return line[:200]
    except OSError:
        pass
    return None
