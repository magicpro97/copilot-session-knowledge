#!/usr/bin/env python3
"""
claude-adapter.py — Parse Claude Code JSONL sessions into knowledge.db format

Reads Claude Code session files (JSONL) and converts them into documents + sections
compatible with the existing Copilot session knowledge database.

Usage:
    python claude-adapter.py                    # Index all Claude sessions
    python claude-adapter.py --stats            # Show what would be indexed
    python claude-adapter.py --dry-run          # Parse but don't write to DB
    python claude-adapter.py --project <hash>   # Index specific project only

Claude Code session structure:
    ~/.claude/projects/<project-hash>/<session-uuid>.jsonl
    Entry types: user, assistant, system, attachment, file-history-snapshot,
                 permission-mode, queue-operation, last-prompt

Cross-platform: Windows, macOS, Linux (WSL). Pure Python stdlib.
"""

import json
import sqlite3
import re
import os
import sys
import hashlib
from pathlib import Path
from datetime import datetime

# Fix Windows console encoding
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SESSION_STATE = Path.home() / ".copilot" / "session-state"
DB_PATH = SESSION_STATE / "knowledge.db"
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"

# Minimum file size to consider (skip near-empty sessions)
MIN_SESSION_BYTES = 1024


def find_claude_sessions() -> list[dict]:
    """Discover all Claude Code session JSONL files.

    Returns list of dicts: {project_hash, session_id, path, size_bytes}
    """
    sessions = []
    if not CLAUDE_PROJECTS.exists():
        return sessions

    for project_dir in CLAUDE_PROJECTS.iterdir():
        if not project_dir.is_dir():
            continue
        project_hash = project_dir.name

        for jsonl_file in project_dir.glob("*.jsonl"):
            if jsonl_file.stat().st_size < MIN_SESSION_BYTES:
                continue
            sessions.append({
                "project_hash": project_hash,
                "session_id": jsonl_file.stem,
                "path": jsonl_file,
                "size_bytes": jsonl_file.stat().st_size,
            })

        # Also check subagents/ directories
        for subdir in project_dir.iterdir():
            if not subdir.is_dir():
                continue
            subagents_dir = subdir / "subagents"
            if subagents_dir.exists():
                for jsonl_file in subagents_dir.glob("*.jsonl"):
                    if jsonl_file.stat().st_size < MIN_SESSION_BYTES:
                        continue
                    sessions.append({
                        "project_hash": project_hash,
                        "session_id": jsonl_file.stem,
                        "path": jsonl_file,
                        "size_bytes": jsonl_file.stat().st_size,
                        "parent_session": subdir.name,
                    })

    return sessions


def parse_jsonl(path: Path) -> list[dict]:
    """Parse JSONL file, skip malformed lines."""
    entries = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass  # Skip malformed lines
    return entries


def extract_text_from_content(content) -> str:
    """Extract readable text from Claude message content.

    Content can be:
    - str: plain text (user messages)
    - list: array of content blocks (assistant messages)
      - {type: "text", text: "..."}
      - {type: "tool_use", name: "...", input: {...}}
      - {type: "tool_result", content: "..."}
    """
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type", "")
            if block_type == "text":
                text = block.get("text", "").strip()
                if text:
                    parts.append(text)
            elif block_type == "tool_use":
                tool_name = block.get("name", "unknown")
                tool_input = block.get("input", {})
                # Extract meaningful tool usage info
                if tool_name in ("Bash", "bash"):
                    cmd = tool_input.get("command", "")
                    if cmd:
                        parts.append(f"[Tool: {tool_name}] {cmd}")
                elif tool_name in ("Edit", "Write", "MultiEdit"):
                    file_path = tool_input.get("file_path", tool_input.get("filePath", ""))
                    if file_path:
                        parts.append(f"[Tool: {tool_name}] {file_path}")
                elif tool_name == "Read":
                    file_path = tool_input.get("file_path", tool_input.get("filePath", ""))
                    if file_path:
                        parts.append(f"[Tool: Read] {file_path}")
                else:
                    parts.append(f"[Tool: {tool_name}]")
            elif block_type == "tool_result":
                result_content = block.get("content", "")
                if isinstance(result_content, str) and len(result_content) < 2000:
                    parts.append(result_content)
        return "\n".join(parts)

    return ""


def parse_session(entries: list[dict]) -> dict:
    """Parse JSONL entries into a structured session.

    Returns: {
        session_id, project_hash, git_branch, cwd,
        timestamp_start, timestamp_end,
        conversations: [{role, text, timestamp}],
        tools_used: [str],
        files_touched: [str],
        summary: str
    }
    """
    session = {
        "session_id": "",
        "project_hash": "",
        "git_branch": "",
        "cwd": "",
        "timestamp_start": "",
        "timestamp_end": "",
        "conversations": [],
        "tools_used": set(),
        "files_touched": set(),
    }

    for entry in entries:
        entry_type = entry.get("type", "")
        timestamp = entry.get("timestamp", "")

        # Track session metadata from first user message
        if entry_type == "user":
            if not session["session_id"]:
                session["session_id"] = entry.get("sessionId", "")
                session["cwd"] = entry.get("cwd", "")
                session["git_branch"] = entry.get("gitBranch", "")
            if not session["timestamp_start"]:
                session["timestamp_start"] = timestamp

            msg = entry.get("message", {})
            content = msg.get("content", "") if isinstance(msg, dict) else ""
            text = extract_text_from_content(content)
            if text:
                session["conversations"].append({
                    "role": "user",
                    "text": text,
                    "timestamp": timestamp,
                })

        elif entry_type == "assistant":
            msg = entry.get("message", {})
            content = msg.get("content", []) if isinstance(msg, dict) else []
            text = extract_text_from_content(content)

            # Track tool usage
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_name = block.get("name", "")
                        if tool_name:
                            session["tools_used"].add(tool_name)
                        # Track file paths from tool usage
                        tool_input = block.get("input", {})
                        for key in ("file_path", "filePath", "path"):
                            fp = tool_input.get(key, "")
                            if fp:
                                session["files_touched"].add(fp)

            if text:
                session["conversations"].append({
                    "role": "assistant",
                    "text": text,
                    "timestamp": timestamp,
                })

        session["timestamp_end"] = timestamp or session["timestamp_end"]

    # Convert sets to sorted lists
    session["tools_used"] = sorted(session["tools_used"])
    session["files_touched"] = sorted(session["files_touched"])

    # Generate summary from first user message
    user_msgs = [c for c in session["conversations"] if c["role"] == "user"]
    if user_msgs:
        first = user_msgs[0]["text"][:500]
        session["summary"] = first
    else:
        session["summary"] = ""

    return session


def session_to_sections(session: dict) -> list[dict]:
    """Convert parsed session into document sections for DB storage.

    Returns list of {section_name, content} dicts.
    """
    sections = []

    # Overview section
    overview_parts = []
    if session["cwd"]:
        overview_parts.append(f"Working directory: {session['cwd']}")
    if session["git_branch"]:
        overview_parts.append(f"Git branch: {session['git_branch']}")
    if session["timestamp_start"]:
        overview_parts.append(f"Started: {session['timestamp_start']}")
    if session["tools_used"]:
        overview_parts.append(f"Tools used: {', '.join(session['tools_used'])}")
    if session["files_touched"]:
        # Show up to 20 files
        files = session["files_touched"][:20]
        overview_parts.append(f"Files touched ({len(session['files_touched'])}): {', '.join(files)}")
    if overview_parts:
        sections.append({
            "section_name": "overview",
            "content": "\n".join(overview_parts),
        })

    # Conversation section — concatenate user+assistant messages
    conv_parts = []
    for msg in session["conversations"]:
        role = msg["role"].upper()
        text = msg["text"]
        # Truncate very long individual messages
        if len(text) > 5000:
            text = text[:5000] + "\n... (truncated)"
        conv_parts.append(f"[{role}]: {text}")

    if conv_parts:
        # Limit total conversation content to ~50KB
        full_conv = "\n\n".join(conv_parts)
        if len(full_conv) > 50000:
            full_conv = full_conv[:50000] + "\n\n... (truncated)"
        sections.append({
            "section_name": "conversation",
            "content": full_conv,
        })

    # Technical details — files and tools
    if session["files_touched"]:
        tech = "## Files Modified/Read\n" + "\n".join(f"- {f}" for f in session["files_touched"])
        sections.append({
            "section_name": "technical_details",
            "content": tech,
        })

    return sections


def index_claude_session(db: sqlite3.Connection, session_info: dict,
                         parsed: dict, incremental: bool) -> bool:
    """Index a single Claude Code session into the knowledge DB. Returns True if indexed."""
    session_id = parsed["session_id"] or session_info["session_id"]
    file_path = str(session_info["path"])
    file_size = session_info["size_bytes"]

    # Compute file hash for incremental checking
    fhash = hashlib.md5(session_info["path"].read_bytes()).hexdigest()

    if incremental:
        existing = db.execute(
            "SELECT file_hash FROM documents WHERE file_path = ?", (file_path,)
        ).fetchone()
        if existing and existing[0] == fhash:
            return False

    # Ensure session row exists
    summary = parsed["summary"][:500] if parsed["summary"] else ""
    db.execute("""
        INSERT INTO sessions (id, path, summary, total_checkpoints, source, indexed_at)
        VALUES (?, ?, ?, 0, 'claude', ?)
        ON CONFLICT(id) DO UPDATE SET
            summary=excluded.summary, source='claude', indexed_at=excluded.indexed_at
    """, (session_id, file_path, summary, datetime.now().isoformat()))

    # Create document entry
    title = f"Claude session {session_id[:8]}"
    if parsed["git_branch"]:
        title += f" ({parsed['git_branch']})"

    preview = summary[:500].replace("\n", " ") if summary else ""

    db.execute("""
        INSERT INTO documents (session_id, doc_type, seq, title, file_path, file_hash,
                              size_bytes, content_preview, source, indexed_at)
        VALUES (?, 'claude-session', 0, ?, ?, ?, ?, ?, 'claude', ?)
        ON CONFLICT(file_path) DO UPDATE SET
            file_hash=excluded.file_hash, size_bytes=excluded.size_bytes,
            content_preview=excluded.content_preview, source='claude', indexed_at=excluded.indexed_at
    """, (session_id, title, file_path, fhash, file_size, preview, datetime.now().isoformat()))

    doc_id = db.execute("SELECT id FROM documents WHERE file_path = ?", (file_path,)).fetchone()[0]

    # Delete old sections and FTS entries
    db.execute("DELETE FROM knowledge_fts WHERE document_id = ?", (doc_id,))
    db.execute("DELETE FROM sections WHERE document_id = ?", (doc_id,))

    # Index sections
    sections = session_to_sections(parsed)
    for sec in sections:
        db.execute(
            "INSERT INTO sections (document_id, section_name, content) VALUES (?, ?, ?)",
            (doc_id, sec["section_name"], sec["content"])
        )
        db.execute("""
            INSERT INTO knowledge_fts (title, section_name, content, doc_type, session_id, document_id)
            VALUES (?, ?, ?, 'claude-session', ?, ?)
        """, (title, sec["section_name"], sec["content"], session_id, doc_id))

    return True


def show_stats():
    """Show what Claude sessions are available for indexing."""
    sessions = find_claude_sessions()
    if not sessions:
        print("No Claude Code sessions found.")
        print(f"Expected location: {CLAUDE_PROJECTS}")
        return

    print(f"\nClaude Code Sessions Found: {len(sessions)}")
    print(f"Location: {CLAUDE_PROJECTS}")
    print()

    # Group by project
    by_project = {}
    for s in sessions:
        proj = s["project_hash"]
        if proj not in by_project:
            by_project[proj] = []
        by_project[proj].append(s)

    for proj, sess_list in sorted(by_project.items()):
        total_kb = sum(s["size_bytes"] for s in sess_list) / 1024
        print(f"  Project: {proj}")
        print(f"    Sessions: {len(sess_list)}, Total: {total_kb:.1f} KB")
        for s in sorted(sess_list, key=lambda x: x["size_bytes"], reverse=True)[:5]:
            parent = f" (subagent of {s['parent_session'][:8]})" if s.get("parent_session") else ""
            print(f"      {s['session_id'][:8]}... {s['size_bytes']/1024:.1f} KB{parent}")
        if len(sess_list) > 5:
            print(f"      ... and {len(sess_list)-5} more")
    print()


def main():
    stats_only = "--stats" in sys.argv
    dry_run = "--dry-run" in sys.argv
    incremental = "--incremental" in sys.argv
    project_filter = None

    if "--project" in sys.argv:
        idx = sys.argv.index("--project")
        if idx + 1 < len(sys.argv):
            project_filter = sys.argv[idx + 1][:256]  # Limit filter length

    if stats_only:
        show_stats()
        return

    sessions = find_claude_sessions()
    if not sessions:
        print("No Claude Code sessions found.")
        print(f"Expected location: {CLAUDE_PROJECTS}")
        return

    if project_filter:
        sessions = [s for s in sessions if project_filter in s["project_hash"]]

    if not DB_PATH.parent.exists():
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Import create_db from build-session-index to ensure schema is ready
    sys.path.insert(0, str(Path(__file__).parent))
    from importlib import import_module
    # Direct import approach
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "build_session_index",
        Path(__file__).parent / "build-session-index.py"
    )
    bsi = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bsi)

    db = bsi.create_db(DB_PATH)

    print(f"Indexing {len(sessions)} Claude Code sessions...")
    print(f"Output: {DB_PATH}")
    print()

    indexed = 0
    skipped = 0
    errors = 0

    for session_info in sessions:
        short_id = session_info["session_id"][:8]
        try:
            entries = parse_jsonl(session_info["path"])
            parsed = parse_session(entries)

            if not parsed["conversations"]:
                print(f"  {short_id}... (no conversations, skipped)")
                skipped += 1
                continue

            if dry_run:
                n_conv = len(parsed["conversations"])
                n_tools = len(parsed["tools_used"])
                print(f"  {short_id}... {n_conv} messages, {n_tools} tools (dry-run)")
                indexed += 1
                continue

            if index_claude_session(db, session_info, parsed, incremental):
                n_conv = len(parsed["conversations"])
                n_sections = len(session_to_sections(parsed))
                print(f"  {short_id}... indexed {n_conv} messages, {n_sections} sections")
                indexed += 1
            else:
                print(f"  {short_id}... (no changes)" if incremental else
                      f"  {short_id}... (skipped)")
                skipped += 1

        except Exception as e:
            print(f"  {short_id}... ERROR: {e}")
            errors += 1

    if not dry_run:
        db.commit()

    print(f"\nDone: {indexed} indexed, {skipped} skipped, {errors} errors")
    db.close()


if __name__ == "__main__":
    main()
