#!/usr/bin/env python3
"""
providers/claude_provider.py — ClaudeProvider implementation.

Reads Anthropic Claude Code JSONL session files from ~/.claude/projects/.

parent_id for subagent sessions is read from the JSONL file's
parentSessionId / parent_session_id field (contract §A-BL-02).
NEVER uses subdir.name.

Pure stdlib. Python 3.10+.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .base import (
    Event,
    MAX_CONTENT_CHARS,
    MAX_TOOL_RESULT_CHARS,
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

_CLAUDE_DEFAULT_ROOT: Path = Path.home() / ".claude" / "projects"
MIN_SESSION_BYTES: int = 1_024
MAX_LINE_BYTES: int = 50_000_000  # contract §Security: skip absurdly long lines


# ──────────────────────────────────────────────
# ClaudeProvider
# ──────────────────────────────────────────────

class ClaudeProvider(SessionProvider):
    """Provider for Anthropic Claude Code JSONL sessions.

    Session structure:
        ~/.claude/projects/
          <project-hash>/
            <session-uuid>.jsonl          ← top-level session
            <subdir>/
              subagents/
                <session-uuid>.jsonl      ← subagent session

    parent_id for subagents comes from the JSONL file itself (parentSessionId
    field), NOT from the directory name (contract §A-BL-02).
    """

    name: str = "claude"

    def _discover_root(self) -> Path | None:
        """Return Claude projects root, respecting CLAUDE_PROJECTS env var."""
        override = os.environ.get("CLAUDE_PROJECTS", "").strip()
        root = Path(override) if override else _CLAUDE_DEFAULT_ROOT
        return root if root.exists() else None

    def list_sessions(self) -> Iterator[SessionMeta]:
        """Yield SessionMeta for each JSONL file under the Claude root.

        Walks two levels:
          1. root/<project-hash>/<session>.jsonl   (top-level)
          2. root/<project-hash>/<subdir>/subagents/<session>.jsonl  (nested)

        For subagent sessions, parent_id is extracted from the JSONL file's
        parentSessionId / parent_session_id field (contract §A-BL-02).
        """
        root = self._discover_root()
        if root is None:
            return

        for project_dir in sorted(root.iterdir()):
            if not project_dir.is_dir():
                continue
            project_hash = project_dir.name

            # Top-level sessions
            for jsonl_file in sorted(project_dir.glob("*.jsonl")):
                size = jsonl_file.stat().st_size
                if size < MIN_SESSION_BYTES:
                    continue
                mtime = jsonl_file.stat().st_mtime
                title = _peek_title(jsonl_file)
                yield SessionMeta(
                    id=jsonl_file.stem,
                    provider=self.name,
                    path=jsonl_file,
                    title=title,
                    mtime=mtime,
                    extra=(("project_hash", project_hash),),
                )

            # Nested subagent sessions
            for subdir in sorted(project_dir.iterdir()):
                if not subdir.is_dir():
                    continue
                subagents_dir = subdir / "subagents"
                if not subagents_dir.exists():
                    continue
                for jsonl_file in sorted(subagents_dir.glob("*.jsonl")):
                    size = jsonl_file.stat().st_size
                    if size < MIN_SESSION_BYTES:
                        continue
                    mtime = jsonl_file.stat().st_mtime
                    # A-BL-02: read parent_id from JSONL, NEVER subdir.name
                    parent_id = _read_parent_session_id(jsonl_file)
                    title = _peek_title(jsonl_file)
                    yield SessionMeta(
                        id=jsonl_file.stem,
                        provider=self.name,
                        path=jsonl_file,
                        title=title,
                        mtime=mtime,
                        parent_id=parent_id,
                        extra=(("project_hash", project_hash),),
                    )

    def iter_events(
        self, session: SessionMeta, *, from_event: int = 0
    ) -> Iterator[Event]:
        """Stream Events from a Claude JSONL file.

        from_event: skip first N events (sequential skip; byte-offset
        optimization deferred to Batch B per contract).

        Malformed JSONL lines are silently skipped (one warning per file).
        Lines larger than MAX_LINE_BYTES are also skipped.
        """
        event_counter = 0
        warned_once = False

        try:
            fh = open(session.path, "r", encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"[ClaudeProvider] Cannot open {session.path}: {exc}", file=sys.stderr)
            return

        with fh:
            for line_num, raw_line in enumerate(fh, 1):
                # Skip absurdly long lines before json.loads (security §MAX_LINE_BYTES)
                if len(raw_line.encode("utf-8", errors="replace")) > MAX_LINE_BYTES:
                    continue

                line = raw_line.strip()
                if not line:
                    continue

                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    if not warned_once:
                        print(
                            f"[ClaudeProvider] Malformed JSONL in {session.path.name}"
                            f" (first bad line: {line_num}), skipping",
                            file=sys.stderr,
                        )
                        warned_once = True
                    continue

                raw_ref = f"{session.path.stem}:L{line_num}"
                entry_type = entry.get("type", "")
                ts = _parse_iso_ts(entry.get("timestamp", ""))

                new_events: list[Event] = []

                if entry_type == "user":
                    msg = entry.get("message", {})
                    if isinstance(msg, dict):
                        content_raw = msg.get("content", "")
                        new_events = _decompose_user_content(
                            content_raw, session.id, event_counter, ts, raw_ref
                        )

                elif entry_type == "assistant":
                    msg = entry.get("message", {})
                    if isinstance(msg, dict):
                        content_raw = msg.get("content", [])
                        new_events = _decompose_assistant_content(
                            content_raw, session.id, event_counter, ts, raw_ref
                        )

                elif entry_type == "system":
                    msg = entry.get("message", {})
                    sys_text = ""
                    if isinstance(msg, dict):
                        sys_text = str(msg.get("content", ""))
                    if sys_text:
                        new_events = [Event(
                            session_id=session.id,
                            event_id=event_counter,
                            ts=ts,
                            kind="system",
                            content=sys_text[:MAX_CONTENT_CHARS],
                            raw_ref=raw_ref,
                        )]

                elif entry_type in ("attachment", "last-prompt"):
                    note_text = json.dumps(entry, ensure_ascii=False)
                    new_events = [Event(
                        session_id=session.id,
                        event_id=event_counter,
                        ts=ts,
                        kind="note",
                        content=note_text[:MAX_CONTENT_CHARS],
                        raw_ref=raw_ref,
                    )]
                # queue-operation, permission-mode, file-history-snapshot:
                # silently skip — low information density, high noise

                for evt in new_events:
                    if event_counter >= from_event:
                        yield evt
                    event_counter += 1


# ──────────────────────────────────────────────
# Private helpers
# ──────────────────────────────────────────────

def _read_parent_session_id(jsonl_path: Path) -> str | None:
    """Peek at a subagent JSONL to extract the parent session UUID.

    Looks for parentSessionId / parent_session_id / parent_session in
    the first 20 JSONL entries. Returns None if not found.

    Contract §A-BL-02: use this value, NEVER the directory name.
    """
    try:
        with open(jsonl_path, "r", encoding="utf-8", errors="replace") as fh:
            for _ in range(20):
                line = fh.readline()
                if not line:
                    break
                try:
                    entry = json.loads(line.strip())
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict):
                    for key in ("parentSessionId", "parent_session_id", "parent_session"):
                        val = entry.get(key)
                        if val and isinstance(val, str):
                            return val
    except OSError:
        pass
    return None


def _peek_title(jsonl_path: Path) -> str | None:
    """Peek at a JSONL to extract the first user message as a title."""
    try:
        with open(jsonl_path, "r", encoding="utf-8", errors="replace") as fh:
            for _ in range(50):
                line = fh.readline()
                if not line:
                    break
                try:
                    entry = json.loads(line.strip())
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue
                if entry.get("type") != "user":
                    continue
                msg = entry.get("message", {})
                if not isinstance(msg, dict):
                    continue
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    return content.strip()[:200]
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "").strip()
                            if text:
                                return text[:200]
    except OSError:
        pass
    return None


def _parse_iso_ts(ts_str: str) -> datetime | None:
    """Parse ISO-8601 timestamp → UTC datetime. Returns None on failure."""
    if not ts_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(ts_str[:26], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _make_hashable_args(d: dict) -> tuple[tuple[str, Any], ...]:
    """Convert a tool_input dict to a hashable tuple-of-pairs.

    Complex values (dicts, lists) are JSON-serialized to strings to
    preserve hashability of the frozen Event dataclass (contract §A-BL-01).
    """
    result = []
    for k, v in sorted(d.items()):
        if isinstance(v, (str, int, float, bool, type(None))):
            result.append((k, v))
        else:
            result.append((k, json.dumps(v, ensure_ascii=False, sort_keys=True)))
    return tuple(result)


def _decompose_user_content(
    content_raw: Any,
    session_id: str,
    base_event_id: int,
    ts: datetime | None,
    raw_ref: str,
) -> list[Event]:
    """Decompose a user JSONL entry into Event(s).

    (a) Plain string → one user_msg Event
    (b) List of blocks → tool_result blocks → tool_result Events;
        text blocks → user_msg Event
    """
    events: list[Event] = []
    counter = base_event_id

    if isinstance(content_raw, str):
        text = content_raw.strip()
        if text:
            events.append(Event(
                session_id=session_id, event_id=counter, ts=ts,
                kind="user_msg", content=text[:MAX_CONTENT_CHARS], raw_ref=raw_ref,
            ))
        return events

    if isinstance(content_raw, list):
        text_parts: list[str] = []
        for block in content_raw:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")
            if btype == "text":
                text_parts.append(block.get("text", "").strip())
            elif btype == "tool_result":
                result_content = block.get("content", "")
                if isinstance(result_content, list):
                    result_content = "\n".join(
                        b.get("text", "") for b in result_content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                result_text = str(result_content)[:MAX_TOOL_RESULT_CHARS]
                tool_id = block.get("tool_use_id") or None
                events.append(Event(
                    session_id=session_id, event_id=counter, ts=ts,
                    kind="tool_result", content=result_text,
                    tool_name=tool_id, tool_result=result_text, raw_ref=raw_ref,
                ))
                counter += 1

        combined_text = "\n".join(p for p in text_parts if p)
        if combined_text:
            events.append(Event(
                session_id=session_id, event_id=counter, ts=ts,
                kind="user_msg", content=combined_text[:MAX_CONTENT_CHARS],
                raw_ref=raw_ref,
            ))

    return events


def _decompose_assistant_content(
    content_raw: Any,
    session_id: str,
    base_event_id: int,
    ts: datetime | None,
    raw_ref: str,
) -> list[Event]:
    """Decompose an assistant JSONL entry into Event(s).

    Emits:
      N × tool_call (one per tool_use block)
      1 × assistant_msg (text blocks only; skipped if empty)
    """
    events: list[Event] = []
    counter = base_event_id
    text_parts: list[str] = []

    if isinstance(content_raw, list):
        for block in content_raw:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")
            if btype == "text":
                text_parts.append(block.get("text", "").strip())
            elif btype == "tool_use":
                tool_name = block.get("name", "unknown")
                tool_input = block.get("input", {})
                summary = _tool_call_summary(tool_name, tool_input)
                tool_args = _make_hashable_args(tool_input) if isinstance(tool_input, dict) else None
                events.append(Event(
                    session_id=session_id, event_id=counter, ts=ts,
                    kind="tool_call", content=summary[:MAX_CONTENT_CHARS],
                    tool_name=tool_name, tool_args=tool_args, raw_ref=raw_ref,
                ))
                counter += 1

    combined_text = "\n".join(p for p in text_parts if p)
    if combined_text:
        events.append(Event(
            session_id=session_id, event_id=counter, ts=ts,
            kind="assistant_msg", content=combined_text[:MAX_CONTENT_CHARS],
            raw_ref=raw_ref,
        ))

    return events


def _tool_call_summary(tool_name: str, tool_input: dict) -> str:
    """Build a short human-readable summary of a tool call."""
    if tool_name.lower() in ("bash",):
        cmd = tool_input.get("command", "")
        return f"[{tool_name}] {cmd}"
    for key in ("file_path", "filePath", "path"):
        fp = tool_input.get(key, "")
        if fp:
            return f"[{tool_name}] {fp}"
    return f"[{tool_name}]"
