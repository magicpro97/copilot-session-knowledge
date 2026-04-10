#!/usr/bin/env python3
"""
query-session.py — Search the Copilot/Claude session knowledge base

Usage:
    python query-session.py "search terms"                     # Full-text search
    python query-session.py "search terms" --semantic          # Hybrid: FTS5 + vector
    python query-session.py "search terms" --type checkpoint   # Filter by doc type
    python query-session.py "search terms" --source claude     # Filter by source
    python query-session.py --list                             # List all sessions
    python query-session.py --list --source copilot            # List only Copilot sessions
    python query-session.py --session <uuid>                   # Show session details
    python query-session.py --recent                           # Show recent activity
    python query-session.py "search" --limit 5                 # Limit results
    python query-session.py "search" --verbose                 # Show full content
    python query-session.py --mistakes                         # Show past mistakes
    python query-session.py --patterns                         # Show learned patterns
    python query-session.py --decisions                        # Show tech decisions
    python query-session.py --detail <id>                      # Full detail of entry
    python query-session.py --context <id>                     # Entry + related context
    python query-session.py --related <id>                     # Show knowledge graph relations
    python query-session.py --graph "spring boot"              # Mini knowledge graph for topic
    python query-session.py "search" --export json             # Export as JSON
    python query-session.py "search" --export markdown         # Export as Markdown

Doc types: checkpoint, research, artifact, plan, claude-session
Knowledge categories: mistake, pattern, decision, tool
Sources: copilot, claude, all (default: all)
Semantic search requires: python embed.py --setup && python embed.py --build
"""

import sqlite3
import sys
import os
import textwrap
from pathlib import Path

# Fix Windows console encoding for Unicode output
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SESSION_STATE = Path.home() / ".copilot" / "session-state"
DB_PATH = SESSION_STATE / "knowledge.db"

# ANSI colors — auto-detect terminal support
def _supports_color() -> bool:
    """Check if terminal supports ANSI colors (cross-platform)."""
    import os, sys
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    if os.name == "nt":
        # Windows 10+ supports ANSI via VT mode
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # Enable VIRTUAL_TERMINAL_PROCESSING
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
            return True
        except Exception:
            return os.environ.get("WT_SESSION") is not None  # Windows Terminal
    return True

_COLOR = _supports_color()
BOLD = "\033[1m" if _COLOR else ""
DIM = "\033[2m" if _COLOR else ""
CYAN = "\033[36m" if _COLOR else ""
GREEN = "\033[32m" if _COLOR else ""
YELLOW = "\033[33m" if _COLOR else ""
MAGENTA = "\033[35m" if _COLOR else ""
RESET = "\033[0m" if _COLOR else ""


def get_db() -> sqlite3.Connection:
    """Connect to the knowledge database."""
    if not DB_PATH.exists():
        print(f"Error: Knowledge database not found at {DB_PATH}")
        print(f"Run 'python build-session-index.py' first to build the index.")
        sys.exit(1)
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    return db


def _sanitize_fts_query(query: str, max_length: int = 500) -> str:
    """Sanitize user input for FTS5 MATCH queries."""
    query = query.strip()[:max_length]
    # Strip FTS5 special operators and syntax characters
    fts_special = set('"*(){}:^')
    cleaned = "".join(c if c not in fts_special else " " for c in query)
    # Remove FTS5 boolean operators used as standalone words
    terms = []
    for t in cleaned.split():
        if t.upper() not in ("OR", "AND", "NOT", "NEAR"):
            terms.append(t)
    if not terms:
        return '""'
    # Wrap each term in quotes for safe prefix matching
    return " ".join(f'"{t}"*' for t in terms)


def search(query: str, doc_type: str = None, limit: int = 10, verbose: bool = False,
           source_filter: str = None):
    """Full-text search across all indexed content."""
    db = get_db()

    # Build FTS5 query - sanitize and wrap terms for prefix matching
    fts_query = _sanitize_fts_query(query)

    sql = """
        SELECT
            fts.title,
            fts.section_name,
            fts.doc_type,
            fts.session_id,
            fts.document_id,
            snippet(knowledge_fts, 2, '>>>', '<<<', '...', 64) as excerpt,
            d.file_path,
            d.size_bytes,
            COALESCE(d.source, 'copilot') as doc_source,
            rank
        FROM knowledge_fts fts
        JOIN documents d ON fts.document_id = d.id
        WHERE knowledge_fts MATCH ?
    """
    params = [fts_query]

    if doc_type:
        sql += " AND fts.doc_type = ?"
        params.append(doc_type)

    if source_filter and source_filter != "all":
        sql += " AND COALESCE(d.source, 'copilot') = ?"
        params.append(source_filter)

    sql += f" ORDER BY rank LIMIT {limit}"

    try:
        results = db.execute(sql, params).fetchall()
    except sqlite3.OperationalError as e:
        print(f"Search error: {e}")
        print(f"Try simpler search terms or use quotes for exact phrases.")
        db.close()
        return

    if not results:
        print(f"No results for: {query}")
        print(f"Tip: Try broader terms or check with --list for available sessions.")
        db.close()
        return

    print(f"\n{BOLD}Found {len(results)} result(s) for: {query}{RESET}\n")

    for i, r in enumerate(results, 1):
        sid = r["session_id"][:8]
        doc_source = r["doc_source"] if "doc_source" in r.keys() else "copilot"
        type_color = {"checkpoint": CYAN, "research": GREEN, "artifact": YELLOW,
                      "plan": MAGENTA, "claude-session": CYAN}.get(r["doc_type"], "")
        source_badge = f" {DIM}[{doc_source}]{RESET}" if doc_source != "copilot" else ""

        if verbose:
            print(f"{BOLD}{i}. {r['title']}{RESET}{source_badge}")
            print(f"   {DIM}Session:{RESET} {sid}...  "
                  f"{type_color}{r['doc_type']}{RESET}  "
                  f"{DIM}Section:{RESET} {r['section_name']}  "
                  f"{DIM}Size:{RESET} {r['size_bytes'] // 1024}KB")

            excerpt = r["excerpt"]
            excerpt = excerpt.replace(">>>", f"{BOLD}{YELLOW}").replace("<<<", f"{RESET}")
            wrapped = textwrap.fill(excerpt, width=90, initial_indent="   ", subsequent_indent="   ")
            print(wrapped)
            print(f"   {DIM}Path: {r['file_path']}{RESET}")
            print()
        else:
            # Compact: title + type + short excerpt
            excerpt_text = r["excerpt"].replace(">>>", "").replace("<<<", "")
            short = excerpt_text[:80].replace("\n", " ").strip()
            print(f"  {BOLD}{i}.{RESET} {r['title'][:55]} "
                  f"{type_color}{r['doc_type']}{RESET}{source_badge}")
            print(f"     {DIM}{short}{RESET}")

    if not verbose:
        print(f"\n{DIM}Use --verbose for full excerpts{RESET}")

    db.close()


def list_sessions(source_filter: str = None):
    """List all indexed sessions."""
    db = get_db()

    print(f"\n{BOLD}Indexed Sessions{RESET}")
    if source_filter and source_filter != "all":
        print(f"{DIM}(filtered: source={source_filter}){RESET}")
    print()
    print(f"{'ID':10s} {'Src':>7s} {'CP':>3s} {'Res':>4s} {'Files':>5s} {'Plan':>4s}  Summary")
    print(f"{'-'*10} {'-'*7} {'-'*3} {'-'*4} {'-'*5} {'-'*4}  {'-'*50}")

    sql = """
        SELECT id, total_checkpoints, total_research, total_files, has_plan,
               SUBSTR(summary, 1, 80) as summary,
               COALESCE(source, 'copilot') as source
        FROM sessions
    """
    params = []
    if source_filter and source_filter != "all":
        sql += " WHERE COALESCE(source, 'copilot') = ?"
        params.append(source_filter)
    sql += " ORDER BY indexed_at DESC"

    for row in db.execute(sql, params):
        sid = row["id"][:8] + ".."
        plan = "Yes" if row["has_plan"] else "-"
        summary = (row["summary"] or "(no summary)")[:50]
        src = row["source"][:7]
        print(f"{sid:10s} {src:>7s} {row['total_checkpoints']:3d} {row['total_research']:4d} "
              f"{row['total_files']:5d} {plan:>4s}  {summary}")

    total = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    docs = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    print(f"\n{DIM}Total: {total} sessions, {docs} documents{RESET}")
    db.close()


def show_session(session_prefix: str):
    """Show details for a specific session."""
    db = get_db()

    row = db.execute(
        "SELECT * FROM sessions WHERE id LIKE ?", (f"{session_prefix}%",)
    ).fetchone()

    if not row:
        print(f"No session found matching: {session_prefix}")
        db.close()
        return

    print(f"\n{BOLD}Session: {row['id']}{RESET}")
    print(f"Path: {row['path']}")
    print(f"Checkpoints: {row['total_checkpoints']}  Research: {row['total_research']}  "
          f"Files: {row['total_files']}  Plan: {'Yes' if row['has_plan'] else 'No'}")
    print(f"\n{BOLD}Summary:{RESET}")
    print(textwrap.fill(row["summary"] or "(no summary)", width=90, initial_indent="  ", subsequent_indent="  "))

    print(f"\n{BOLD}Documents:{RESET}")
    for doc in db.execute("""
        SELECT doc_type, seq, title, size_bytes
        FROM documents WHERE session_id = ?
        ORDER BY doc_type, seq
    """, (row["id"],)):
        type_color = {"checkpoint": CYAN, "research": GREEN, "artifact": YELLOW, "plan": MAGENTA}.get(doc["doc_type"], "")
        seq = f"#{doc['seq']:02d}" if doc["seq"] > 0 else "   "
        print(f"  {type_color}{doc['doc_type']:12s}{RESET} {seq} {doc['title']} ({doc['size_bytes']//1024}KB)")

    db.close()


def show_recent(limit: int = 10):
    """Show recently indexed documents."""
    db = get_db()

    print(f"\n{BOLD}Recently Indexed Documents{RESET}\n")

    for doc in db.execute("""
        SELECT d.*, s.id as session_id
        FROM documents d
        JOIN sessions s ON d.session_id = s.id
        ORDER BY d.indexed_at DESC
        LIMIT ?
    """, (limit,)):
        sid = doc["session_id"][:8]
        type_color = {"checkpoint": CYAN, "research": GREEN, "artifact": YELLOW, "plan": MAGENTA}.get(doc["doc_type"], "")
        print(f"  {type_color}{doc['doc_type']:12s}{RESET} {sid}.. {doc['title']} ({doc['size_bytes']//1024}KB)")

    db.close()


def show_knowledge(category: str, limit: int = 20, export_fmt: str = None,
                   source_filter: str = None, verbose: bool = False):
    """Show extracted knowledge entries by category."""
    db = get_db()

    sql = """
        SELECT ke.id, ke.category, ke.title, ke.content, ke.tags,
               ke.confidence, ke.session_id, ke.occurrence_count,
               COALESCE(ke.source, 'copilot') as source
        FROM knowledge_entries ke
        WHERE ke.category = ?
    """
    params = [category]
    if source_filter and source_filter != "all":
        sql += " AND COALESCE(ke.source, 'copilot') = ?"
        params.append(source_filter)
    sql += " ORDER BY ke.confidence DESC, ke.occurrence_count DESC LIMIT ?"
    params.append(limit)

    try:
        rows = db.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        print(f"No knowledge entries found. Run 'python extract-knowledge.py' first.")
        db.close()
        return

    if not rows:
        print(f"No {category} entries found.")
        db.close()
        return

    if export_fmt == "json":
        _export_json([dict(r) for r in rows])
        db.close()
        return
    if export_fmt == "markdown":
        _export_markdown_knowledge(rows, category)
        db.close()
        return

    print(f"\n{BOLD}{category.upper()} entries ({len(rows)} results){RESET}\n")

    for i, r in enumerate(rows, 1):
        sid = r["session_id"][:8]
        conf = f"{r['confidence']:.1f}"
        count = f" x{r['occurrence_count']}" if r["occurrence_count"] > 1 else ""

        if verbose:
            tags = f" [{r['tags']}]" if r["tags"] else ""
            print(f"{BOLD}{i}. {r['title']}{RESET}")
            print(f"   {DIM}ID:{RESET} #{r['id']}  "
                  f"{DIM}Session:{RESET} {sid}..  "
                  f"{DIM}Confidence:{RESET} {conf}{count}  "
                  f"{DIM}Tags:{RESET}{tags}")
            content_preview = r["content"][:300].replace("\n", "\n   ")
            print(f"   {content_preview}")
            if len(r["content"]) > 300:
                print(f"   {DIM}... ({len(r['content'])} chars total){RESET}")
            print()
        else:
            # Compact: one line per entry with ID
            first_line = r["content"].split("\n")[0][:80] if r["content"] else ""
            print(f"  {DIM}#{r['id']:>4d}{RESET} {BOLD}{r['title'][:60]}{RESET} "
                  f"{DIM}[conf:{conf}{count}]{RESET}")
            if first_line:
                print(f"        {DIM}{first_line}{RESET}")

    if not verbose:
        print(f"\n{DIM}Use --detail <id> for full content, --verbose for expanded view{RESET}")

    db.close()


def show_detail(entry_id: int):
    """Show full detail of a specific knowledge entry by ID."""
    db = get_db()
    row = db.execute("""
        SELECT ke.*, COALESCE(ke.source, 'copilot') as src,
               d.title as doc_title, d.doc_type, d.file_path
        FROM knowledge_entries ke
        LEFT JOIN documents d ON ke.document_id = d.id
        WHERE ke.id = ?
    """, (entry_id,)).fetchone()

    if not row:
        print(f"No knowledge entry with ID {entry_id}")
        db.close()
        return

    print(f"\n{BOLD}Knowledge Entry #{entry_id}{RESET}")
    print(f"{'='*60}")
    print(f"{BOLD}Category:{RESET} {row['category'].upper()}")
    print(f"{BOLD}Title:{RESET} {row['title']}")
    print(f"{BOLD}Source:{RESET} {row['src']}")
    print(f"{BOLD}Session:{RESET} {row['session_id'][:12]}...")
    print(f"{BOLD}Confidence:{RESET} {row['confidence']:.2f}  "
          f"{DIM}Occurrences:{RESET} {row['occurrence_count']}")
    if row['tags']:
        print(f"{BOLD}Tags:{RESET} {row['tags']}")
    if row['doc_title']:
        print(f"{BOLD}Document:{RESET} {row['doc_title']} ({row['doc_type']})")
    if row['first_seen']:
        print(f"{BOLD}First seen:{RESET} {row['first_seen']}")
    if row['last_seen']:
        print(f"{BOLD}Last seen:{RESET} {row['last_seen']}")

    print(f"\n{BOLD}Content:{RESET}")
    print(f"{'-'*60}")
    print(row['content'])
    print(f"{'-'*60}")

    db.close()


def show_context(entry_id: int):
    """Show a knowledge entry plus related entries from same session/category."""
    db = get_db()
    row = db.execute("""
        SELECT ke.*, COALESCE(ke.source, 'copilot') as src
        FROM knowledge_entries ke WHERE ke.id = ?
    """, (entry_id,)).fetchone()

    if not row:
        print(f"No knowledge entry with ID {entry_id}")
        db.close()
        return

    # Show the main entry (compact)
    print(f"\n{BOLD}Context for: {row['title']}{RESET}")
    print(f"{DIM}Category: {row['category']} | Session: {row['session_id'][:12]}...{RESET}")
    print(f"\n{row['content'][:500]}")
    if len(row['content']) > 500:
        print(f"{DIM}... ({len(row['content'])} chars, use --detail {entry_id} for full){RESET}")

    # Find related: same session
    same_session = db.execute("""
        SELECT id, category, title, confidence
        FROM knowledge_entries
        WHERE session_id = ? AND id != ?
        ORDER BY confidence DESC LIMIT 10
    """, (row['session_id'], entry_id)).fetchall()

    if same_session:
        print(f"\n{BOLD}Same session entries:{RESET}")
        for r in same_session:
            print(f"  {DIM}#{r['id']}{RESET} [{r['category']}] {r['title']} "
                  f"{DIM}(conf: {r['confidence']:.1f}){RESET}")

    # Find related: same category, different session
    same_category = db.execute("""
        SELECT id, category, title, confidence, session_id
        FROM knowledge_entries
        WHERE category = ? AND session_id != ? AND id != ?
        ORDER BY confidence DESC LIMIT 5
    """, (row['category'], row['session_id'], entry_id)).fetchall()

    if same_category:
        print(f"\n{BOLD}Related {row['category']} entries (other sessions):{RESET}")
        for r in same_category:
            sid = r['session_id'][:8]
            print(f"  {DIM}#{r['id']}{RESET} {r['title']} "
                  f"{DIM}(session: {sid}.. conf: {r['confidence']:.1f}){RESET}")

    # Check knowledge_relations table if it exists
    try:
        relations = db.execute("""
            SELECT kr.relation_type, kr.confidence as rel_conf,
                   ke.id as rel_id, ke.category, ke.title
            FROM knowledge_relations kr
            JOIN knowledge_entries ke ON ke.id = CASE
                WHEN kr.source_id = ? THEN kr.target_id
                ELSE kr.source_id END
            WHERE kr.source_id = ? OR kr.target_id = ?
            ORDER BY kr.confidence DESC LIMIT 10
        """, (entry_id, entry_id, entry_id)).fetchall()
        if relations:
            print(f"\n{BOLD}Linked entries:{RESET}")
            for r in relations:
                print(f"  {DIM}#{r['rel_id']}{RESET} --{r['relation_type']}--> "
                      f"[{r['category']}] {r['title']} "
                      f"{DIM}(conf: {r['rel_conf']:.1f}){RESET}")
    except sqlite3.OperationalError:
        print("⚠ knowledge_relations table not found; skipping linked entries", file=sys.stderr)


def show_related(entry_id: int):
    """Show entries related to the given entry via knowledge graph."""
    db = get_db()
    entry = db.execute(
        "SELECT id, category, title, content, session_id FROM knowledge_entries WHERE id = ?",
        (entry_id,),
    ).fetchone()
    if not entry:
        print(f"No knowledge entry with ID {entry_id}")
        db.close()
        return

    print(f"\n{BOLD}Relations for: {entry['title'][:80]}{RESET}")
    print(f"{DIM}Category: {entry['category']} | Session: {entry['session_id'][:12]}...{RESET}\n")

    try:
        outgoing = db.execute("""
            SELECT kr.relation_type, kr.confidence, ke.id, ke.category, ke.title
            FROM knowledge_relations kr
            JOIN knowledge_entries ke ON kr.target_id = ke.id
            WHERE kr.source_id = ?
            ORDER BY kr.confidence DESC
        """, (entry_id,)).fetchall()

        incoming = db.execute("""
            SELECT kr.relation_type, kr.confidence, ke.id, ke.category, ke.title
            FROM knowledge_relations kr
            JOIN knowledge_entries ke ON kr.source_id = ke.id
            WHERE kr.target_id = ?
            ORDER BY kr.confidence DESC
        """, (entry_id,)).fetchall()
    except sqlite3.OperationalError:
        print("No relations found. Run extract-knowledge.py to generate relations.")
        db.close()
        return

    if not outgoing and not incoming:
        print("No relations found. Run extract-knowledge.py to generate relations.")
        db.close()
        return

    if outgoing:
        print(f"{BOLD}\u2192 Outgoing ({len(outgoing)}):{RESET}")
        for r in outgoing[:15]:
            print(f"  [{r['relation_type']}] {DIM}#{r['id']}{RESET} [{r['category']}] "
                  f"{r['title'][:60]} {DIM}(conf: {r['confidence']:.1f}){RESET}")

    if incoming:
        print(f"\n{BOLD}\u2190 Incoming ({len(incoming)}):{RESET}")
        for r in incoming[:15]:
            print(f"  [{r['relation_type']}] {DIM}#{r['id']}{RESET} [{r['category']}] "
                  f"{r['title'][:60]} {DIM}(conf: {r['confidence']:.1f}){RESET}")

    db.close()


def show_graph(topic: str):
    """Show a mini knowledge graph around a topic."""
    db = get_db()

    fts_query = _sanitize_fts_query(topic)

    try:
        matches = db.execute("""
            SELECT ke.id, ke.category, ke.title, ke.confidence
            FROM ke_fts fts
            JOIN knowledge_entries ke ON fts.rowid = ke.id
            WHERE ke_fts MATCH ?
            ORDER BY rank
            LIMIT 5
        """, (fts_query,)).fetchall()
    except sqlite3.OperationalError:
        print(f"No knowledge entries matching '{topic}'")
        db.close()
        return

    if not matches:
        print(f"No knowledge entries matching '{topic}'")
        db.close()
        return

    print(f"\n{BOLD}Knowledge Graph: {topic}{RESET}")
    print("=" * 60)

    entry_ids = [m['id'] for m in matches]

    for m in matches:
        eid = m['id']
        print(f"\n{BOLD}\u25cf #{eid} [{m['category']}] {m['title'][:60]}{RESET} "
              f"{DIM}(conf: {m['confidence']:.1f}){RESET}")

        try:
            relations = db.execute("""
                SELECT kr.relation_type, kr.confidence,
                       CASE WHEN kr.source_id = ? THEN kr.target_id ELSE kr.source_id END as other_id,
                       ke.category, ke.title
                FROM knowledge_relations kr
                JOIN knowledge_entries ke ON ke.id = CASE WHEN kr.source_id = ? THEN kr.target_id ELSE kr.source_id END
                WHERE kr.source_id = ? OR kr.target_id = ?
                ORDER BY kr.confidence DESC
                LIMIT 8
            """, (eid, eid, eid, eid)).fetchall()

            for r in relations:
                marker = "\u2194" if r['other_id'] in entry_ids else "\u2192"
                print(f"  {marker} [{r['relation_type']}] {DIM}#{r['other_id']}{RESET} "
                      f"[{r['category']}] {r['title'][:50]}")
        except sqlite3.OperationalError:
            print(f"⚠ knowledge_relations table not found; skipping relations for #{eid}", file=sys.stderr)

    try:
        placeholders = ",".join("?" * len(entry_ids))
        total_rels = db.execute(
            f"SELECT COUNT(*) FROM knowledge_relations WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})",
            entry_ids + entry_ids
        ).fetchone()[0]
    except sqlite3.OperationalError:
        total_rels = 0

    print(f"\n--- {len(matches)} entries, {total_rels} relations ---")
    db.close()


def search_knowledge(query: str, limit: int = 10, export_fmt: str = None):
    """Search knowledge entries with FTS5."""
    db = get_db()

    fts_query = _sanitize_fts_query(query)

    try:
        rows = db.execute("""
            SELECT ke.*, snippet(ke_fts, 1, '>>>', '<<<', '...', 48) as excerpt
            FROM ke_fts fts
            JOIN knowledge_entries ke ON fts.rowid = ke.id
            WHERE ke_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """, (fts_query, limit)).fetchall()
    except sqlite3.OperationalError:
        # ke_fts table doesn't exist — fall back to regular search
        rows = []

    if export_fmt == "json" and rows:
        _export_json([dict(r) for r in rows])
        db.close()
        return

    if rows:
        print(f"\n{BOLD}Knowledge entries matching: {query} ({len(rows)} results){RESET}\n")
        for i, r in enumerate(rows, 1):
            sid = r["session_id"][:8]
            excerpt = r["excerpt"].replace(">>>", f"{BOLD}{YELLOW}").replace("<<<", f"{RESET}")
            print(f"{BOLD}{i}. [{r['category']}] {r['title']}{RESET}")
            print(f"   {DIM}Session:{RESET} {sid}..  {DIM}Tags:{RESET} {r['tags']}")
            print(f"   {excerpt}")
            print()

    db.close()


def _export_json(rows: list):
    """Export results as JSON to stdout."""
    import json
    import io
    clean = []
    for r in rows:
        d = dict(r)
        d.pop("excerpt", None)
        clean.append(d)
    output = json.dumps(clean, indent=2, ensure_ascii=False, default=str)
    # Handle Windows console encoding issues
    try:
        print(output)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(output.encode("utf-8"))
        sys.stdout.buffer.write(b"\n")


def _export_markdown_knowledge(rows: list, category: str):
    """Export knowledge entries as Markdown."""
    print(f"# {category.title()} Knowledge Entries\n")
    for i, r in enumerate(rows, 1):
        print(f"## {i}. {r['title']}\n")
        print(f"- **Session**: `{r['session_id'][:8]}...`")
        print(f"- **Confidence**: {r['confidence']:.1f}")
        if r["tags"]:
            print(f"- **Tags**: {r['tags']}")
        print(f"\n{r['content'][:1000]}\n")
        print("---\n")


def export_search_results(results: list, fmt: str):
    """Export search results in specified format."""
    if fmt == "json":
        _export_json([dict(r) for r in results])
    elif fmt == "markdown":
        print("# Search Results\n")
        for i, r in enumerate(results, 1):
            print(f"## {i}. {r['title']}\n")
            print(f"- **Session**: `{r['session_id'][:8]}...`")
            print(f"- **Type**: {r['doc_type']}")
            print(f"- **Section**: {r['section_name']}")
            print(f"\n{r['excerpt']}\n")
            print("---\n")


def semantic_search(query: str, limit: int = 10, verbose: bool = False):
    """Hybrid search: FTS5 keyword + vector semantic, merged with RRF."""
    try:
        tools_dir = Path(__file__).parent
        sys.path.insert(0, str(tools_dir))
        from embed import load_config, hybrid_search, ensure_embedding_tables
    except ImportError:
        print("Error: embed.py not found. Falling back to keyword search.")
        search(query, limit=limit, verbose=verbose)
        return

    config = load_config()
    db = get_db()
    ensure_embedding_tables(db)

    results = hybrid_search(db, query, config, limit=limit)

    if not results:
        print(f"No results for: {query}")
        print("Tip: Try keyword search (without --semantic) or broader terms.")
        db.close()
        return

    # Determine search mode used
    sources = set()
    for r in results:
        for s in r.get("source", "").split("+"):
            if s:
                sources.add(s)
    mode_label = "+".join(sorted(sources)) if sources else "keyword"

    print(f"\n{BOLD}Hybrid search ({mode_label}): {len(results)} result(s) for: {query}{RESET}\n")

    for i, r in enumerate(results, 1):
        sid = r.get("session_id", "?")[:8]
        doc_type = r.get("doc_type", "?")
        source = r.get("source", "?")
        rrf = r.get("rrf_score", 0)

        type_color = {"checkpoint": CYAN, "research": GREEN, "artifact": YELLOW,
                      "plan": MAGENTA, "mistake": YELLOW, "pattern": GREEN,
                      "decision": CYAN, "tool": MAGENTA}.get(doc_type, "")

        # Source indicator
        source_parts = []
        if "keyword" in source:
            source_parts.append(f"{GREEN}FTS{RESET}")
        if "semantic" in source:
            source_parts.append(f"{CYAN}VEC{RESET}")
        if "tfidf" in source:
            source_parts.append(f"{MAGENTA}TFIDF{RESET}")
        source_label = "+".join(source_parts) if source_parts else source

        print(f"{BOLD}{i}. {r.get('title', '?')}{RESET}")
        print(f"   {DIM}Session:{RESET} {sid}..  "
              f"{type_color}{doc_type}{RESET}  "
              f"{DIM}Match:{RESET} {source_label}  "
              f"{DIM}RRF:{RESET} {rrf:.4f}")

        # Show scores if verbose
        if verbose:
            scores = []
            if "fts_rank" in r:
                scores.append(f"fts_rank={r['fts_rank']:.2f}")
            if "vec_score" in r:
                scores.append(f"vec_sim={r['vec_score']:.4f}")
            if "tfidf_score" in r:
                scores.append(f"tfidf={r['tfidf_score']:.4f}")
            if scores:
                print(f"   {DIM}Scores: {', '.join(scores)}{RESET}")

        # Excerpt
        excerpt = r.get("excerpt", "")[:200]
        if excerpt:
            excerpt = excerpt.replace(">>>", f"{BOLD}{YELLOW}").replace("<<<", f"{RESET}")
            wrapped = textwrap.fill(excerpt, width=90,
                                    initial_indent="   ", subsequent_indent="   ")
            print(wrapped)

        if verbose and "section_name" in r:
            print(f"   {DIM}Section: {r['section_name']}{RESET}")

        print()

    db.close()


def print_usage():
    print(__doc__)


def main():
    args = sys.argv[1:]

    if not args or "--help" in args or "-h" in args:
        print_usage()
        return

    # Parse --source filter (copilot, claude, all)
    source_filter = None
    if "--source" in args:
        idx = args.index("--source")
        if idx + 1 < len(args):
            source_filter = args[idx + 1]

    if "--list" in args:
        list_sessions(source_filter)
        return

    if "--recent" in args:
        limit = 10
        if "--limit" in args:
            idx = args.index("--limit")
            limit = int(args[idx + 1])
        show_recent(limit)
        return

    if "--session" in args:
        idx = args.index("--session")
        if idx + 1 < len(args):
            show_session(args[idx + 1])
        else:
            print("Error: --session requires a session ID prefix")
        return

    if "--detail" in args:
        idx = args.index("--detail")
        if idx + 1 < len(args):
            show_detail(int(args[idx + 1]))
        else:
            print("Error: --detail requires an entry ID")
        return

    if "--context" in args:
        idx = args.index("--context")
        if idx + 1 < len(args):
            show_context(int(args[idx + 1]))
        else:
            print("Error: --context requires an entry ID")
        return

    if "--related" in args:
        idx = args.index("--related")
        if idx + 1 < len(args):
            show_related(int(args[idx + 1]))
        else:
            print("Error: --related requires an entry ID")
        return

    if "--graph" in args:
        idx = args.index("--graph")
        if idx + 1 < len(args):
            show_graph(args[idx + 1])
        else:
            print("Error: --graph requires a topic")
        return

    # Knowledge category shortcuts
    export_fmt = None
    if "--export" in args:
        idx = args.index("--export")
        export_fmt = args[idx + 1] if idx + 1 < len(args) else "json"

    limit = 10
    if "--limit" in args:
        idx = args.index("--limit")
        limit = int(args[idx + 1]) if idx + 1 < len(args) else 10

    verbose = "--verbose" in args or "-v" in args

    for shortcut, category in [("--mistakes", "mistake"), ("--patterns", "pattern"),
                                ("--decisions", "decision"), ("--tools", "tool")]:
        if shortcut in args:
            show_knowledge(category, limit, export_fmt, source_filter, verbose)
            return

    # Check for semantic mode
    use_semantic = "--semantic" in args or "-s" in args

    # Default: search mode
    query_parts = []
    doc_type = None
    verbose = False

    i = 0
    while i < len(args):
        if args[i] == "--type" and i + 1 < len(args):
            doc_type = args[i + 1]
            i += 2
        elif args[i] in ("--limit", "--export", "--source"):
            i += 2  # skip flag + value (already parsed)
        elif args[i] in ("--verbose", "-v"):
            verbose = True
            i += 1
        elif args[i] in ("--semantic", "-s"):
            i += 1
        elif args[i].startswith("--"):
            i += 1  # skip unknown flags
        else:
            query_parts.append(args[i])
            i += 1

    query = " ".join(query_parts)
    if not query:
        print("Error: Search query required")
        print_usage()
        return

    if use_semantic:
        semantic_search(query, limit, verbose)
    elif export_fmt:
        search(query, doc_type, limit, verbose, source_filter)
    else:
        search(query, doc_type, limit, verbose, source_filter)
        # Also search knowledge entries
        search_knowledge(query, limit=5)


if __name__ == "__main__":
    main()
