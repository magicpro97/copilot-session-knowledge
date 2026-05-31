#!/usr/bin/env python3
"""sk session compact — compress a session's knowledge into a structured checkpoint.

Creates a session_checkpoint entry with Goal/Progress/Key Decisions/Changed Files/
Next Steps/Critical Context sections that packs session knowledge into ~500 tokens.

Usage:
    python session-compact.py                   # compact most recent session
    python session-compact.py <session_id>       # compact specific session
    python session-compact.py --dry-run          # preview without writing
    python session-compact.py --list             # list existing checkpoints
    python session-compact.py --json             # JSON output
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8")


def _db_path() -> Path:
    if e := os.environ.get("SK_DB_PATH"):
        return Path(e)
    for p in [
        Path.home() / ".copilot/knowledge.db",
        Path(__file__).parent / "sessions.db",
        Path.home() / ".copilot/tools/sessions.db",
    ]:
        if p.exists():
            return p
    return Path.home() / ".copilot/knowledge.db"


def _most_recent_session(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT id FROM sessions ORDER BY indexed_at DESC LIMIT 1").fetchone()
    return row[0] if row else None


def _get_entries(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    """Get all knowledge entries for a session."""
    rows = conn.execute(
        """SELECT id, category, title, content, tags, priority
           FROM knowledge_entries
           WHERE session_id = ?
           ORDER BY category, priority""",
        (session_id,),
    ).fetchall()
    return [dict(zip(["id", "category", "title", "content", "tags", "priority"], r, strict=False)) for r in rows]


def _existing_checkpoint(conn: sqlite3.Connection, session_id: str) -> dict | None:
    """Return existing session_checkpoint for this session if any."""
    row = conn.execute(
        """SELECT id, title, content FROM knowledge_entries
           WHERE session_id = ? AND category = 'session_checkpoint'
           LIMIT 1""",
        (session_id,),
    ).fetchone()
    return dict(zip(["id", "title", "content"], row, strict=False)) if row else None


def _build_compact_prompt(session_id: str, entries: list[dict]) -> str:
    """Build the LLM prompt for structured compaction."""
    entry_text = "\n".join(
        f"[{e['category']}|{e['priority']}] {e['title']}: {e['content'][:200]}"
        for e in entries[:50]  # cap at 50 entries to stay within LLM context
    )
    return f"""You are compacting a coding session's knowledge entries into a structured checkpoint.
Session: {session_id}
Total entries: {len(entries)}

Knowledge entries:
{entry_text}

Create a structured checkpoint with EXACTLY these sections (keep each under 100 words):
## Goal
What was being built or fixed in this session?

## Progress
What was accomplished? What was merged/shipped?

## Key Decisions
What architecture/approach decisions were made?

## Changed Files
Which files were modified? (bullet list, file paths only)

## Next Steps
What remains to be done?

## Critical Context
What must a future agent know to continue this work correctly? (pitfalls, constraints, discoveries)

Be concise. The entire checkpoint must fit in 500 tokens."""


def _call_llm(prompt: str) -> str | None:
    """Call LLM via gh copilot suggest or fallback to template."""
    try:
        result = subprocess.run(
            ["gh", "copilot", "suggest", "-t", "shell", prompt[:4000]],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _template_compact(session_id: str, entries: list[dict]) -> str:
    """Create a template checkpoint without LLM (fallback)."""
    by_cat: dict[str, list] = {}
    for e in entries:
        by_cat.setdefault(e["category"], []).append(e)

    parts = [f"## Goal\nSession {session_id[:20]}... — {len(entries)} knowledge entries"]

    if "mistake" in by_cat:
        titles = [e["title"][:60] for e in by_cat["mistake"][:5]]
        parts.append("## Key Decisions\nMistakes/fixes: " + "; ".join(titles))

    if "feature" in by_cat or "pattern" in by_cat:
        cat = "feature" if "feature" in by_cat else "pattern"
        titles = [e["title"][:60] for e in by_cat[cat][:5]]
        parts.append("## Progress\nFeatures/patterns: " + "; ".join(titles))

    parts.append("## Next Steps\n(Not determined — run sk briefing for context)")
    parts.append("## Critical Context\n" + "; ".join(e["title"][:40] for e in entries[:3] if e.get("priority") == "P0"))
    return "\n\n".join(parts)


def _store_checkpoint(
    conn: sqlite3.Connection,
    session_id: str,
    content: str,
    existing_id: int | None,
) -> int:
    """Store or update the session_checkpoint entry."""
    now = datetime.now(timezone.utc).isoformat()
    title = f"Session checkpoint: {session_id[:30]}"
    if existing_id:
        conn.execute(
            "UPDATE knowledge_entries SET content=?, last_seen=? WHERE id=?",
            (content, now, existing_id),
        )
        conn.commit()
        return existing_id
    else:
        cur = conn.execute(
            """INSERT INTO knowledge_entries
               (session_id, category, title, content, first_seen, last_seen, priority, source)
               VALUES (?,?,?,?,?,?,'P0','compact')""",
            (session_id, "session_checkpoint", title, content, now, now),
        )
        conn.commit()
        return cur.lastrowid


def _list_checkpoints(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT ke.id, ke.session_id, ke.title, ke.first_seen,
                  length(ke.content) AS chars
           FROM knowledge_entries ke
           WHERE ke.category = 'session_checkpoint'
           ORDER BY ke.first_seen DESC
           LIMIT 20"""
    ).fetchall()
    return [dict(zip(["id", "session_id", "title", "first_seen", "chars"], r, strict=False)) for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description="Compact session knowledge into structured checkpoint")
    parser.add_argument("session_id", nargs="?", help="Session ID (default: most recent)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--list", action="store_true", dest="list_mode")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument(
        "--no-llm",
        action="store_true",
        dest="no_llm",
        help="Skip LLM call, use template-based compaction",
    )
    args = parser.parse_args()

    db_path = _db_path()
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    if args.list_mode:
        checkpoints = _list_checkpoints(conn)
        if args.as_json:
            print(json.dumps(checkpoints, indent=2))
        else:
            print(f"Session checkpoints ({len(checkpoints)}):")
            for c in checkpoints:
                print(f"  #{c['id']} {c['first_seen'][:10]} {c['title'][:60]} ({c['chars']} chars)")
        conn.close()
        return

    session_id = args.session_id or _most_recent_session(conn)
    if not session_id:
        print("No sessions found.", file=sys.stderr)
        sys.exit(1)

    entries = _get_entries(conn, session_id)
    if not entries:
        print(f"No knowledge entries found for session {session_id[:30]}...", file=sys.stderr)
        sys.exit(1)

    existing = _existing_checkpoint(conn, session_id)

    if args.no_llm:
        content = _template_compact(session_id, entries)
    else:
        prompt = _build_compact_prompt(session_id, entries)
        llm_result = _call_llm(prompt)
        content = llm_result if llm_result else _template_compact(session_id, entries)

    est_tokens = len(content) // 4

    if args.dry_run:
        print(f"DRY RUN — would compact session {session_id[:30]}...")
        print(f"Entries: {len(entries)}, Checkpoint: {len(content)} chars (~{est_tokens} tokens)")
        if existing:
            print(f"Would UPDATE existing checkpoint #{existing['id']}")
        else:
            print("Would CREATE new checkpoint entry")
        print("\n" + "─" * 40)
        print(content[:1000])
        conn.close()
        return

    entry_id = _store_checkpoint(conn, session_id, content, existing["id"] if existing else None)

    if args.as_json:
        print(
            json.dumps(
                {
                    "session_id": session_id,
                    "entry_id": entry_id,
                    "entries_compacted": len(entries),
                    "checkpoint_chars": len(content),
                    "est_tokens": est_tokens,
                    "updated": existing is not None,
                }
            )
        )
    else:
        action = "Updated" if existing else "Created"
        print(f"✅ {action} checkpoint #{entry_id} for session {session_id[:30]}...")
        print(f"   Compacted {len(entries)} entries → {len(content)} chars (~{est_tokens} tokens)")

    conn.close()


if __name__ == "__main__":
    main()
