#!/usr/bin/env python3
"""
briefing.py — Auto-generate context briefing from knowledge base

Before starting any task, run this to get relevant past experience injected as context.
AI agents can call this automatically to avoid repeating past mistakes.

Usage:
    python briefing.py "implement user CRUD"              # Compact briefing (default)
    python briefing.py "implement user CRUD" --full       # Full markdown briefing
    python briefing.py "fix Docker compose" --compact     # XML compact for AI context
    python briefing.py "fix Docker compose" --json        # JSON output
    python briefing.py "spring boot migration" --limit 5  # More results per category
    python briefing.py --auto                             # Auto-detect from git/plan
    python briefing.py --auto --full                      # Full briefing with auto-detect
    python briefing.py --wakeup                           # Ultra-compact wake-up (~170 tokens)
    python briefing.py --room copyToGroup                 # Filter by room
    python briefing.py --wing backend                     # Filter by wing

Default output is compact (~500 tokens): titles + 1-line summaries with entry IDs.
Use --wakeup for ultra-compact AI wake-up context (~170 tokens).
Use --full for complete content with tags, confidence scores, and full text.
"""

import json
import os
import sqlite3
import subprocess
import sys
import textwrap
import time
from pathlib import Path

# Fix Windows console encoding
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
        print("Error: Knowledge database not found. Run build-session-index.py first.",
              file=sys.stderr)
        sys.exit(1)
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    return db


def auto_detect_context() -> str:
    """Auto-detect task context — extract keywords from git + plan."""
    keywords = set()

    # Git branch name → extract feature keywords
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5
        ).stdout.strip()
        if branch and branch != "HEAD":
            # "feature/model-management" → "model management"
            parts = branch.replace("/", "-").replace("_", "-").split("-")
            keywords.update(p for p in parts if len(p) > 2
                           and p not in ("feature", "fix", "chore", "update", "and"))
    except Exception as e:
        print(f"⚠ Git branch detection failed: {e}", file=sys.stderr)

    # Git recent commit messages → extract subject words
    try:
        log = subprocess.run(
            ["git", "--no-pager", "log", "--oneline", "-5", "--format=%s"],
            capture_output=True, text=True, timeout=5
        ).stdout.strip()
        if log:
            for line in log.splitlines():
                # Strip conventional commit prefix
                msg = line.split(":", 1)[-1].strip() if ":" in line else line
                words = msg.split()
                keywords.update(w for w in words if len(w) > 2
                               and w.lower() not in ("the", "and", "for", "add", "fix",
                                                      "update", "with", "from", "that"))
    except Exception as e:
        print(f"⚠ Git log parsing failed: {e}", file=sys.stderr)

    # Plan.md title/first meaningful line
    for session_dir in sorted(SESSION_STATE.iterdir(), reverse=True):
        plan = session_dir / "plan.md"
        if plan.exists():
            try:
                for line in plan.read_text(encoding="utf-8", errors="replace").splitlines()[:5]:
                    line = line.strip().lstrip("#").strip()
                    if line and not line.startswith("|") and len(line) > 5:
                        keywords.update(w for w in line.split() if len(w) > 2)
                        break
            except Exception as e:
                print(f"⚠ Plan file parsing failed: {e}", file=sys.stderr)
            break

    # Git modified file paths → extract module/feature names
    try:
        status = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True, text=True, timeout=5
        ).stdout.strip()
        if status:
            for fpath in status.splitlines()[:10]:
                parts = Path(fpath).parts
                keywords.update(p for p in parts if len(p) > 3
                               and not p.startswith(".") and "." not in p)
    except Exception as e:
        print(f"⚠ Git status parsing failed: {e}", file=sys.stderr)

    query = " ".join(sorted(keywords)[:15])if keywords else "general development"
    return query


def _sanitize_fts_query(query: str, max_length: int = 500) -> str:
    """Sanitize user input for FTS5 MATCH queries."""
    query = query.strip()[:max_length]
    fts_special = set('"*(){}:^')
    cleaned = "".join(c if c not in fts_special else " " for c in query)
    terms = [t for t in cleaned.split() if t.upper() not in ("OR", "AND", "NOT", "NEAR")]
    if not terms:
        return '""'
    return " ".join(f'"{t}"*' for t in terms)


def search_knowledge_entries(db: sqlite3.Connection, query: str,
                             category: str, limit: int = 3,
                             min_confidence: float = 0.0) -> list[dict]:
    """Search knowledge entries by category using FTS5."""
    fts_query = _sanitize_fts_query(query)

    results = []
    try:
        rows = db.execute("""
            SELECT ke.id, ke.title, ke.content, ke.tags,
                   ke.confidence, ke.session_id, ke.occurrence_count
            FROM ke_fts fts
            JOIN knowledge_entries ke ON fts.rowid = ke.id
            WHERE ke_fts MATCH ?
            AND ke.category = ?
            AND ke.confidence >= ?
            ORDER BY ke.confidence DESC, rank
            LIMIT ?
        """, (fts_query, category, min_confidence, limit)).fetchall()
        results.extend([dict(r) for r in rows])
    except sqlite3.OperationalError:
        pass

    return results


def search_semantic(db: sqlite3.Connection, query: str,
                    category: str, limit: int = 3,
                    min_confidence: float = 0.0) -> list[dict]:
    """Search knowledge entries using vector embeddings."""
    try:
        sys.path.insert(0, str(TOOLS_DIR))
        from embed import load_config, resolve_provider, call_embedding_api
        from embed import vector_search, ensure_embedding_tables, search_tfidf

        config = load_config()
        ensure_embedding_tables(db)

        # Try API embedding
        provider_name, provider_config = resolve_provider(config)
        query_vector = None

        if provider_name and provider_config:
            try:
                vecs = call_embedding_api([query], provider_config)
                query_vector = vecs[0]
            except Exception as e:
                print(f"⚠ Embedding API call failed: {e}", file=sys.stderr)

        if query_vector:
            vec_results = vector_search(db, query_vector,
                                        source_type="knowledge", limit=limit * 3)
            results = []
            for st, sid, score in vec_results:
                if score < 0.3:
                    continue
                row = db.execute("""
                    SELECT id, title, content, tags, confidence,
                           session_id, occurrence_count, category
                    FROM knowledge_entries WHERE id = ? AND category = ?
                    AND confidence >= ?
                """, (sid, category, min_confidence)).fetchone()
                if row:
                    results.append(dict(row))
                if len(results) >= limit:
                    break
            return results

        # TF-IDF fallback
        if config.get("fallback") == "tfidf":
            row = db.execute(
                "SELECT model_blob FROM tfidf_model WHERE id = 1"
            ).fetchone()
            if row and row[0]:
                tfidf_results = search_tfidf(query, row[0], limit=limit * 3)
                results = []
                for section_id, score in tfidf_results:
                    if score < 0.05:
                        continue
                    # Map section to knowledge entries from same session
                    ke_row = db.execute("""
                        SELECT ke.* FROM knowledge_entries ke
                        WHERE ke.category = ?
                        ORDER BY ke.confidence DESC
                        LIMIT ?
                    """, (category, limit)).fetchall()
                    for r in ke_row:
                        d = dict(r)
                        if d not in results:
                            results.append(d)
                    if len(results) >= limit:
                        break
                return results[:limit]

    except (ImportError, Exception):
        pass

    return []


def search_past_work(db: sqlite3.Connection, query: str, limit: int = 3) -> list[dict]:
    """Search past work/checkpoints related to query."""
    fts_query = query.strip()
    if not any(c in fts_query for c in ['"', "*", "OR", "AND", "NOT", "NEAR"]):
        terms = fts_query.split()
        fts_query = " ".join(f'"{t}"*' for t in terms)

    results = []
    try:
        rows = db.execute("""
            SELECT fts.title, fts.doc_type, fts.session_id,
                   snippet(knowledge_fts, 2, '', '', '...', 40) as excerpt
            FROM knowledge_fts fts
            WHERE knowledge_fts MATCH ?
            AND fts.doc_type IN ('checkpoint', 'research')
            ORDER BY rank
            LIMIT ?
        """, (fts_query, limit)).fetchall()
        results = [dict(r) for r in rows]
    except sqlite3.OperationalError:
        pass

    return results


def generate_subagent_context(query: str, limit: int = 3,
                              min_confidence: float = 0.5) -> str:
    """Generate compact context block for injecting into sub-agent prompts.

    Output is ~200-400 tokens — minimal overhead for sub-agent context windows.
    Format: plain text with no markdown formatting for easy prompt embedding.
    """
    db = get_db()
    lines = ["[KNOWLEDGE CONTEXT — from past sessions]"]

    for cat, label in [("mistake", "AVOID"), ("pattern", "USE"),
                       ("decision", "NOTE"), ("tool", "CONFIG")]:
        fts = search_knowledge_entries(db, query, cat, limit,
                                       min_confidence=min_confidence)
        sem = search_semantic(db, query, cat, limit,
                              min_confidence=min_confidence)
        # Merge and dedup by id
        seen = set()
        entries = []
        for e in fts + sem:
            eid = e[0] if isinstance(e, (list, tuple)) else e.get("id", id(e))
            if eid not in seen:
                seen.add(eid)
                entries.append(e)

        for e in entries[:limit]:
            if isinstance(e, (list, tuple)):
                title = e[1] if len(e) > 1 else str(e)
            else:
                title = e.get("title", str(e))
            # Truncate title for compactness
            title_short = str(title)[:120]
            lines.append(f"  [{label}] {title_short}")

    if len(lines) == 1:
        return ""  # No relevant context found

    lines.append("[END KNOWLEDGE CONTEXT]")
    return "\n".join(lines)


def generate_briefing(query: str, limit: int = 3, fmt: str = "md",
                      full: bool = False, min_confidence: float = 0.5) -> str:
    """Generate a structured briefing from the knowledge base."""
    db = get_db()

    # Gather knowledge by category
    categories = {
        "mistake": {"emoji": "⚠️", "title": "Past Mistakes to Avoid",
                    "desc": "These mistakes were encountered before. Avoid repeating them."},
        "pattern": {"emoji": "✅", "title": "Proven Patterns to Follow",
                    "desc": "These patterns worked well in the past."},
        "decision": {"emoji": "🏗️", "title": "Architecture Decisions",
                     "desc": "Past decisions for reference — respect unless requirements changed."},
        "tool": {"emoji": "🔧", "title": "Relevant Tools & Configs",
                 "desc": "Tools and configurations used in similar work."},
    }

    briefing_data = {}
    global_seen_titles = set()  # Cross-category dedup

    for cat, meta in categories.items():
        # Combine FTS5 + semantic results, deduplicate
        fts_results = search_knowledge_entries(db, query, cat, limit,
                                                min_confidence=min_confidence)
        sem_results = search_semantic(db, query, cat, limit,
                                      min_confidence=min_confidence)

        merged = []
        for r in fts_results + sem_results:
            title = r.get("title", "")
            if title not in global_seen_titles:
                global_seen_titles.add(title)
                merged.append(r)

        briefing_data[cat] = merged[:limit]

    # Past related work
    past_work = search_past_work(db, query, limit)

    db.close()

    # Check if we have anything
    total_entries = sum(len(v) for v in briefing_data.values()) + len(past_work)
    if total_entries == 0:
        if fmt == "json":
            return json.dumps({"query": query, "briefing": None,
                               "message": "No relevant past experience found."}, indent=2)
        return f"No relevant past experience found for: {query}\n"

    # Format output
    if fmt == "json":
        return _format_json(query, briefing_data, past_work, categories)
    elif fmt == "compact":
        return _format_compact(query, briefing_data, past_work, categories)
    elif full:
        return _format_markdown(query, briefing_data, past_work, categories)
    else:
        return _format_default(query, briefing_data, past_work, categories)


def _format_default(query: str, data: dict, past_work: list, categories: dict) -> str:
    """Compact default format: titles + 1-line summaries (~500 tokens)."""
    lines = []
    lines.append(f"📋 Briefing: {query}")
    lines.append("")

    for cat, meta in categories.items():
        entries = data.get(cat, [])
        if not entries:
            continue

        lines.append(f"{meta['emoji']} {meta['title']}")
        for entry in entries:
            eid = entry.get("id", "?")
            title = entry.get("title", "Untitled")
            if len(title) > 80:
                title = title[:77] + "..."
            # Extract 1-line summary from content
            content = entry.get("content", "")
            summary = ""
            for ln in content.split("\n"):
                ln = ln.strip().lstrip("-").lstrip("*").lstrip("0123456789.").strip()
                if (ln and len(ln) > 15
                        and not ln.startswith("#")
                        and not ln.startswith("|")
                        and not ln.startswith(">")
                        and not ln.startswith("```")):
                    summary = ln[:80]
                    break
            if summary:
                lines.append(f"  #{eid} {title} — {summary}")
            else:
                lines.append(f"  #{eid} {title}")
        lines.append("")

    if past_work:
        lines.append("📚 Related Past Work")
        for w in past_work:
            sid = w.get("session_id", "?")[:8]
            title = w.get("title", "?")
            if len(title) > 80:
                title = title[:77] + "..."
            lines.append(f"  [{w.get('doc_type', '?')}] {title} (session {sid})")
        lines.append("")

    total = sum(len(v) for v in data.values()) + len(past_work)
    lines.append(f"({total} entries) "
                 f"Use --full for complete content, "
                 f"or query-session.py --detail <id> for specific entry")

    return "\n".join(lines)


def _format_markdown(query: str, data: dict, past_work: list, categories: dict) -> str:
    """Format briefing as Markdown."""
    lines = []
    lines.append(f"# 📋 Pre-Task Briefing")
    lines.append(f"")
    lines.append(f"**Task**: {query}")
    lines.append(f"**Generated**: {time.strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"")
    lines.append("---")
    lines.append("")

    for cat, meta in categories.items():
        entries = data.get(cat, [])
        if not entries:
            continue

        lines.append(f"## {meta['emoji']} {meta['title']}")
        lines.append(f"")
        lines.append(f"_{meta['desc']}_")
        lines.append("")

        for i, entry in enumerate(entries, 1):
            title = entry.get("title", "Untitled")
            content = entry.get("content", "")
            tags = entry.get("tags", "")
            confidence = entry.get("confidence", 0)
            count = entry.get("occurrence_count", 1)

            lines.append(f"### {i}. {title}")
            if tags:
                lines.append(f"Tags: `{tags}` | Confidence: {confidence:.1f}"
                             + (f" | Seen {count}x" if count > 1 else ""))
            lines.append("")

            # Limit content preview
            preview = content[:500]
            if len(content) > 500:
                preview += "..."
            lines.append(preview)
            lines.append("")

        lines.append("")

    if past_work:
        lines.append("## 📚 Related Past Work")
        lines.append("")
        lines.append("_Previous sessions that worked on similar topics._")
        lines.append("")
        for i, work in enumerate(past_work, 1):
            sid = work.get("session_id", "?")[:8]
            lines.append(f"{i}. **{work.get('title', '?')}** "
                         f"({work.get('doc_type', '?')}, session `{sid}..`)")
            excerpt = work.get("excerpt", "")[:200]
            if excerpt:
                lines.append(f"   {excerpt}")
            lines.append("")

    lines.append("---")
    lines.append(f"_Briefing from knowledge.db — "
                 f"{sum(len(v) for v in data.values())} entries + "
                 f"{len(past_work)} past work refs_")

    return "\n".join(lines)


def _format_json(query: str, data: dict, past_work: list, categories: dict) -> str:
    """Format briefing as JSON."""
    output = {
        "query": query,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "sections": {}
    }

    for cat, meta in categories.items():
        entries = data.get(cat, [])
        if entries:
            output["sections"][cat] = {
                "title": meta["title"],
                "entries": [
                    {
                        "title": e.get("title", ""),
                        "content": e.get("content", "")[:500],
                        "tags": e.get("tags", ""),
                        "confidence": e.get("confidence", 0),
                    }
                    for e in entries
                ]
            }

    if past_work:
        output["sections"]["past_work"] = {
            "title": "Related Past Work",
            "entries": [
                {
                    "title": w.get("title", ""),
                    "type": w.get("doc_type", ""),
                    "session": w.get("session_id", "")[:8],
                    "excerpt": w.get("excerpt", "")[:200],
                }
                for w in past_work
            ]
        }

    return json.dumps(output, indent=2, ensure_ascii=False)


def _format_compact(query: str, data: dict, past_work: list, categories: dict) -> str:
    """Compact format optimized for AI agent context injection."""
    lines = []
    lines.append(f"<briefing task=\"{query[:100]}\">\n")

    for cat, meta in categories.items():
        entries = data.get(cat, [])
        if not entries:
            continue
        lines.append(f"<{cat}s>")
        for entry in entries:
            title = entry.get("title", "")[:80]
            content = entry.get("content", "")
            # Extract first meaningful sentence/line
            first_line = ""
            for ln in content.split("\n"):
                ln = ln.strip().lstrip("-").lstrip("*").lstrip("0123456789.").strip()
                if (ln and len(ln) > 15
                        and not ln.startswith("#")
                        and not ln.startswith("|")
                        and not ln.startswith(">")
                        and not ln.startswith("```")
                        and "phỏng vấn" not in ln.lower()
                        and "điểm" not in ln.lower()[:20]):
                    first_line = ln[:200]
                    break
            if not first_line:
                first_line = content[:150].replace("\n", " ")
            lines.append(f"- {title}: {first_line}")
        lines.append(f"</{cat}s>\n")

    if past_work:
        lines.append("<past_work>")
        for w in past_work:
            sid = w.get("session_id", "?")[:8]
            lines.append(f"- [{w.get('doc_type', '?')}] {w.get('title', '?')} "
                         f"(session {sid})")
        lines.append("</past_work>\n")

    lines.append("</briefing>")
    return "\n".join(lines)


def generate_wakeup() -> str:
    """Ultra-compact wake-up summary (~170 tokens) for session start.

    Outputs key project context, current branch, top mistakes/patterns,
    and recent decisions in a terse format designed for AI consumption.
    """
    db = get_db()
    lines = []

    # Team & project info (static)
    lines.append("TEAM: linhnt102-fpt | NES (Nhật) | NEC (customer)")
    lines.append("PROJECT: NEO-MATCH 救急搬送 | CDK+Lambda+Expo | dev2")

    # Current branch
    try:
        import subprocess
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL, text=True).strip()
        lines.append(f"BRANCH: {branch}")
    except Exception:
        lines.append("BRANCH: (unknown)")

    # Wakeup config overrides
    try:
        rows = db.execute(
            "SELECT key, value FROM wakeup_config ORDER BY key"
        ).fetchall()
        for r in rows:
            lines.append(f"{r['key'].upper()}: {r['value']}")
    except sqlite3.OperationalError:
        pass

    # Top mistakes (3)
    try:
        rows = db.execute("""
            SELECT title FROM knowledge_entries
            WHERE category = 'mistake' AND confidence >= 0.5
            ORDER BY occurrence_count DESC, confidence DESC
            LIMIT 3
        """).fetchall()
        if rows:
            items = " | ".join(f"({i+1}) {r['title'][:50]}" for i, r in enumerate(rows))
            lines.append(f"TOP-MISTAKES: {items}")
    except sqlite3.OperationalError:
        pass

    # Top patterns (3)
    try:
        rows = db.execute("""
            SELECT title FROM knowledge_entries
            WHERE category = 'pattern' AND confidence >= 0.5
            ORDER BY occurrence_count DESC, confidence DESC
            LIMIT 3
        """).fetchall()
        if rows:
            items = " | ".join(f"({i+1}) {r['title'][:50]}" for i, r in enumerate(rows))
            lines.append(f"TOP-PATTERNS: {items}")
    except sqlite3.OperationalError:
        pass

    # Recent decisions (3)
    try:
        rows = db.execute("""
            SELECT title FROM knowledge_entries
            WHERE category = 'decision'
            ORDER BY noted_at DESC
            LIMIT 3
        """).fetchall()
        if rows:
            items = " | ".join(f"({i+1}) {r['title'][:50]}" for i, r in enumerate(rows))
            lines.append(f"RECENT-DECISIONS: {items}")
    except sqlite3.OperationalError:
        pass

    db.close()
    return "\n".join(lines)


def search_by_wing_room(wing: str = "", room: str = "",
                        limit: int = 10) -> str:
    """Search knowledge entries filtered by wing and/or room."""
    db = get_db()
    conditions = []
    params = []

    if wing:
        conditions.append("wing = ?")
        params.append(wing)
    if room:
        conditions.append("room = ?")
        params.append(room)

    if not conditions:
        db.close()
        return "Error: specify --wing and/or --room"

    where = " AND ".join(conditions)
    params.append(limit)

    rows = db.execute(f"""
        SELECT id, category, title, content, tags, wing, room, confidence
        FROM knowledge_entries
        WHERE {where}
        ORDER BY confidence DESC, occurrence_count DESC
        LIMIT ?
    """, params).fetchall()

    if not rows:
        db.close()
        return f"No entries found for wing={wing!r} room={room!r}"

    lines = [f"Found {len(rows)} entries (wing={wing!r} room={room!r}):\n"]
    for r in rows:
        first_line = r["content"].split("\n")[0][:120] if r["content"] else ""
        lines.append(f"  [{r['category']}] #{r['id']} {r['title']}")
        lines.append(f"    {first_line}")
    db.close()
    return "\n".join(lines)


def main():
    args = sys.argv[1:]

    if not args or "--help" in args or "-h" in args:
        print(__doc__)
        return

    # Handle --wakeup mode (ultra-compact, no query needed)
    if "--wakeup" in args:
        print(generate_wakeup())
        return

    # Handle --wing/--room search
    wing_filter = ""
    room_filter = ""
    if "--wing" in args:
        idx = args.index("--wing")
        wing_filter = args[idx + 1] if idx + 1 < len(args) else ""
    if "--room" in args:
        idx = args.index("--room")
        room_filter = args[idx + 1] if idx + 1 < len(args) else ""

    if wing_filter or room_filter:
        limit = 10
        if "--limit" in args:
            idx = args.index("--limit")
            limit = int(args[idx + 1]) if idx + 1 < len(args) else 10
        print(search_by_wing_room(wing=wing_filter, room=room_filter,
                                  limit=limit))
        return

    # Parse arguments
    fmt = "md"
    limit = 3
    auto_mode = "--auto" in args
    full_mode = "--full" in args

    if "--format" in args:
        idx = args.index("--format")
        fmt = args[idx + 1] if idx + 1 < len(args) else "md"

    if "--json" in args:
        fmt = "json"

    if "--compact" in args:
        fmt = "compact"

    if "--limit" in args:
        idx = args.index("--limit")
        limit = int(args[idx + 1]) if idx + 1 < len(args) else 3

    min_confidence = 0.5  # Default: filter out low-quality entries
    if "--min-confidence" in args:
        idx = args.index("--min-confidence")
        min_confidence = float(args[idx + 1]) if idx + 1 < len(args) else 0.5
    if "--all" in args:
        min_confidence = 0.0  # Show everything including low-confidence

    subagent_mode = "--for-subagent" in args

    if auto_mode:
        query = auto_detect_context()
        print(f"[briefing] auto-detected: {query}", file=sys.stderr)
    else:
        query_parts = [a for a in args if not a.startswith("--")
                       and a not in ("md", "json", "compact", str(limit))]
        # Filter out values that follow flags
        flag_values = set()
        for i, a in enumerate(args):
            if a in ("--format", "--limit", "--min-confidence") and i + 1 < len(args):
                flag_values.add(args[i + 1])
        query_parts = [a for a in query_parts if a not in flag_values]
        query = " ".join(query_parts)

    if not query:
        print("Error: Provide a task description or use --auto")
        return

    if subagent_mode:
        output = generate_subagent_context(query, limit=limit,
                                           min_confidence=min_confidence)
    else:
        output = generate_briefing(query, limit=limit, fmt=fmt, full=full_mode,
                                  min_confidence=min_confidence)
    print(output)


if __name__ == "__main__":
    main()
