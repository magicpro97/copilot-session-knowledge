#!/usr/bin/env python3
"""
learn.py — Record new knowledge from AI agent sessions

Allows AI agents (Copilot, Claude, Cursor) to write learnings back to the
shared knowledge base during or after work.

Usage:
    python learn.py --mistake "Title" "Description of what went wrong and fix"
    python learn.py --pattern "Title" "Description of what works well"
    python learn.py --decision "Title" "Architecture decision and rationale"
    python learn.py --tool "Title" "Tool/config that was useful"

    python learn.py --mistake "Title" "Description" --tags "docker,compose"
    python learn.py --mistake "Title" "Description" --session abc123
    python learn.py --mistake "Title" "Description" --confidence 0.8

    python learn.py --from-file notes.md          # Bulk import from markdown
    python learn.py --list                        # List recent entries
    python learn.py --stats                       # Show knowledge stats
"""

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TOOLS_DIR = Path(__file__).parent
SESSION_STATE = Path.home() / ".copilot" / "session-state"
DB_PATH = SESSION_STATE / "knowledge.db"


def get_db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        print("Error: Knowledge DB not found. Run build-session-index.py first.",
              file=sys.stderr)
        sys.exit(1)
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    return db


def detect_session_id() -> str:
    """Try to detect current session ID from environment or recent sessions."""
    # Check if there's a session env var
    sid = os.environ.get("COPILOT_SESSION_ID", "")
    if sid:
        return sid

    # Find most recently modified session
    if SESSION_STATE.exists():
        sessions = []
        for d in SESSION_STATE.iterdir():
            if d.is_dir() and len(d.name) > 8 and "-" in d.name:
                try:
                    mtime = max(f.stat().st_mtime for f in d.rglob("*") if f.is_file())
                    sessions.append((mtime, d.name))
                except (ValueError, OSError):
                    pass
        if sessions:
            sessions.sort(reverse=True)
            return sessions[0][1]

    return "manual"


def add_entry(category: str, title: str, content: str,
              tags: str = "", session_id: str = None,
              confidence: float = None) -> int:
    """Add a knowledge entry to the database. Returns entry ID."""
    db = get_db()

    if not session_id:
        session_id = detect_session_id()

    if confidence is None:
        confidence = {"mistake": 0.7, "pattern": 0.6,
                      "decision": 0.8, "tool": 0.5}.get(category, 0.5)

    now = time.strftime("%Y-%m-%dT%H:%M:%S")

    # Check for existing entry with same title in same category
    existing = db.execute("""
        SELECT id, occurrence_count, content FROM knowledge_entries
        WHERE category = ? AND title = ?
        ORDER BY confidence DESC LIMIT 1
    """, (category, title)).fetchone()

    if existing:
        # Update existing: bump occurrence count, update content if longer
        new_count = existing["occurrence_count"] + 1
        new_content = content if len(content) > len(existing["content"]) else existing["content"]
        new_confidence = min(1.0, confidence + 0.05 * (new_count - 1))

        db.execute("""
            UPDATE knowledge_entries
            SET content = ?, occurrence_count = ?, confidence = ?,
                last_seen = ?, tags = CASE WHEN ? != '' THEN ? ELSE tags END
            WHERE id = ?
        """, (new_content, new_count, new_confidence, now,
              tags, tags, existing["id"]))
        entry_id = existing["id"]
        print(f"  Updated existing entry #{entry_id} (seen {new_count}x, "
              f"confidence → {new_confidence:.2f})")
    else:
        # Insert new entry
        db.execute("""
            INSERT INTO knowledge_entries
                (category, title, content, tags, confidence, session_id,
                 occurrence_count, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
        """, (category, title, content, tags, confidence,
              session_id, now, now))
        entry_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        print(f"  Added new {category} #{entry_id}")

    # Update FTS index
    _update_fts(db, entry_id, title, content, tags, category)

    # Generate embedding for the new entry
    _embed_entry(db, entry_id, title, content)

    db.commit()
    db.close()
    return entry_id


def _update_fts(db: sqlite3.Connection, entry_id: int,
                title: str, content: str, tags: str, category: str):
    """Update the standalone FTS5 table for this entry."""
    try:
        db.execute("DELETE FROM ke_fts WHERE rowid = ?", (entry_id,))
        db.execute("""
            INSERT INTO ke_fts (rowid, title, content, tags, category)
            VALUES (?, ?, ?, ?, ?)
        """, (entry_id, title, content, tags, category))
    except sqlite3.OperationalError:
        pass  # ke_fts might not exist yet


def _embed_entry(db: sqlite3.Connection, entry_id: int,
                 title: str, content: str):
    """Generate and store embedding for a single entry."""
    try:
        sys.path.insert(0, str(TOOLS_DIR))
        from embed import (load_config, resolve_provider, call_embedding_api,
                           ensure_embedding_tables, serialize_vector)

        config = load_config()
        provider_name, provider_config = resolve_provider(config)

        if not provider_name:
            return

        ensure_embedding_tables(db)
        text = f"{title}: {content[:2000]}"
        vecs = call_embedding_api([text], provider_config)

        if vecs:
            blob = serialize_vector(vecs[0])
            now = time.strftime("%Y-%m-%dT%H:%M:%S")
            db.execute("""
                INSERT OR REPLACE INTO embeddings
                    (source_type, source_id, provider, model, dimensions,
                     vector, text_preview, created_at)
                VALUES ('knowledge', ?, ?, ?, ?, ?, ?, ?)
            """, (entry_id, provider_name, provider_config["model"],
                  provider_config.get("dimensions", 768), blob,
                  title[:200], now))
            print(f"  Embedded with {provider_name}")
    except Exception as e:
        print(f"  [info] Embedding skipped: {e}", file=sys.stderr)


def import_from_file(filepath: str):
    """Bulk import knowledge entries from a markdown file.

    Expected format:
    ## mistake: Title Here
    Content describing the mistake...

    ## pattern: Title Here
    Content describing the pattern...
    """
    path = Path(filepath)
    if not path.exists():
        print(f"Error: File not found: {filepath}")
        return

    content = path.read_text(encoding="utf-8", errors="replace")
    entries = []
    current = None

    for line in content.splitlines():
        if line.startswith("## "):
            if current:
                entries.append(current)
            # Parse "## category: Title"
            rest = line[3:].strip()
            if ":" in rest:
                cat, title = rest.split(":", 1)
                cat = cat.strip().lower()
                if cat in ("mistake", "pattern", "decision", "tool"):
                    current = {"category": cat, "title": title.strip(), "lines": []}
                else:
                    current = None
            else:
                current = None
        elif current is not None:
            current["lines"].append(line)

    if current:
        entries.append(current)

    if not entries:
        print("No entries found. Use format: ## category: Title")
        return

    print(f"Importing {len(entries)} entries from {filepath}...")
    for entry in entries:
        content = "\n".join(entry["lines"]).strip()
        if content:
            add_entry(entry["category"], entry["title"], content)

    print(f"Done. Imported {len(entries)} entries.")


def list_recent(limit: int = 10):
    """List recently added/updated knowledge entries."""
    db = get_db()

    print(f"\nRecent Knowledge Entries (last {limit})\n")
    rows = db.execute("""
        SELECT id, category, title, confidence, occurrence_count,
               last_seen, session_id
        FROM knowledge_entries
        ORDER BY last_seen DESC
        LIMIT ?
    """, (limit,)).fetchall()

    for r in rows:
        sid = r["session_id"][:8] if r["session_id"] else "?"
        count = f" ×{r['occurrence_count']}" if r["occurrence_count"] > 1 else ""
        print(f"  #{r['id']:3d} [{r['category']:8s}] {r['title'][:60]}")
        print(f"       conf={r['confidence']:.2f}{count}  "
              f"session={sid}..  {r['last_seen'] or '?'}")

    db.close()


def show_stats():
    """Show knowledge base statistics."""
    db = get_db()

    print("\nKnowledge Base Statistics\n")

    total = db.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
    print(f"Total entries: {total}")

    for row in db.execute("""
        SELECT category, COUNT(*) as cnt,
               ROUND(AVG(confidence), 2) as avg_conf,
               SUM(occurrence_count) as total_seen
        FROM knowledge_entries
        GROUP BY category ORDER BY cnt DESC
    """):
        print(f"  {row['category']:10s}: {row['cnt']:3d} entries  "
              f"avg_conf={row['avg_conf']}  total_seen={row['total_seen']}")

    # Embedding coverage
    try:
        emb_count = db.execute(
            "SELECT COUNT(*) FROM embeddings WHERE source_type='knowledge'"
        ).fetchone()[0]
        print(f"\nEmbedded: {emb_count}/{total}")
    except sqlite3.OperationalError:
        pass

    db.close()


def main():
    args = sys.argv[1:]

    if not args or "--help" in args or "-h" in args:
        print(__doc__)
        return

    if "--list" in args:
        limit = 10
        if "--limit" in args:
            idx = args.index("--limit")
            limit = int(args[idx + 1]) if idx + 1 < len(args) else 10
        list_recent(limit)
        return

    if "--stats" in args:
        show_stats()
        return

    if "--from-file" in args:
        idx = args.index("--from-file")
        if idx + 1 < len(args):
            import_from_file(args[idx + 1])
        else:
            print("Error: --from-file requires a filepath")
        return

    # Parse category flag
    category = None
    for flag, cat in [("--mistake", "mistake"), ("--pattern", "pattern"),
                      ("--decision", "decision"), ("--tool", "tool")]:
        if flag in args:
            category = cat
            break

    if not category:
        print("Error: Specify a category: --mistake, --pattern, --decision, or --tool")
        print("Run --help for usage.")
        return

    # Parse optional flags
    tags = ""
    session_id = None
    confidence = None

    if "--tags" in args:
        idx = args.index("--tags")
        tags = args[idx + 1] if idx + 1 < len(args) else ""

    if "--session" in args:
        idx = args.index("--session")
        session_id = args[idx + 1] if idx + 1 < len(args) else None

    if "--confidence" in args:
        idx = args.index("--confidence")
        confidence = float(args[idx + 1]) if idx + 1 < len(args) else None

    # Extract title and content (positional args after flag)
    positional = []
    skip_next = False
    for i, a in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if a in ("--mistake", "--pattern", "--decision", "--tool"):
            continue
        if a in ("--tags", "--session", "--confidence", "--limit"):
            skip_next = True
            continue
        if a.startswith("--"):
            continue
        positional.append(a)

    if len(positional) < 2:
        print(f'Error: Need title and content. Example:')
        print(f'  python learn.py --{category} "Title" "Description"')
        return

    title = positional[0]
    content = " ".join(positional[1:])

    print(f"Recording {category}...")
    add_entry(category, title, content, tags=tags,
              session_id=session_id, confidence=confidence)
    print("Done.")


if __name__ == "__main__":
    main()
