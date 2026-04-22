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
    python query-session.py --relate "entity"                   # Query entity relations (new graph)
    python query-session.py --wings                             # List wings with counts
    python query-session.py --rooms                             # List rooms with counts
    python query-session.py --rooms backend                     # Rooms in a specific wing
    python query-session.py --graph-stats                       # Knowledge graph statistics
    python query-session.py "search" --export json             # Export as JSON
    python query-session.py "search" --export markdown         # Export as Markdown
    python query-session.py --file src/auth.py                 # Entries touching a file
    python query-session.py --module auth                      # Entries for a module/directory
    python query-session.py --diff                             # Entries for current git diff files
    python query-session.py --task memory-surface              # Entries for a specific task ID
    python query-session.py "search" --budget 2000             # Cap output to 2000 chars
    python query-session.py --file src/auth.py --compact       # Titles-only with ~token hint
    python query-session.py --task my-task --compact           # Compact task recall

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


def _analyze_query_strictness(query: str) -> str:
    """Classify query retrieval strictness from lightweight signals.

    Returns 'strict', 'medium', or 'broad'.

    - 'strict':  1-2 terms, or has file/path separators or extensions, or
                 high average word length (domain-specific technical terms).
                 Callers use exact token matching and a tighter confidence threshold.
    - 'broad':   6+ words with 2+ natural-language stopwords present.
                 Callers use OR-conjunction matching and a relaxed threshold.
    - 'medium':  Everything else — the default prefix-match behaviour.

    No network or LLM calls.  Pure Python stdlib.
    """
    import re as _re
    words = query.strip().split()
    if not words:
        return "medium"

    wc = len(words)
    strict_score = 0
    broad_score = 0

    if wc <= 2:
        strict_score += 2
    elif wc >= 6:
        broad_score += 2

    # Technical path/identifier signals (file extensions, separators, long numeric IDs)
    _tech = _re.compile(r'\.[a-z]{1,5}(?:\b|$)|[/\\]|\d{4,}|_[a-z]')
    if any(_tech.search(w) for w in words):
        strict_score += 2

    avg_len = sum(len(w) for w in words) / wc
    if avg_len >= 7:
        strict_score += 1
    elif avg_len <= 3.5:
        broad_score += 1

    # Natural-language stopwords → query reads like a sentence → broad recall
    _STOPWORDS = frozenset({"the", "for", "and", "with", "that", "this", "when",
                            "how", "what", "why", "should", "use", "using", "from",
                            "into", "over", "not", "does", "have", "are", "was",
                            "we", "our", "they", "them", "it", "its", "by", "as",
                            "at", "an", "a", "is", "in", "on", "to", "be", "or",
                            "do", "so", "if"})
    stopword_count = sum(1 for w in words if w.lower() in _STOPWORDS)
    if stopword_count >= 2:
        broad_score += 2

    if strict_score > broad_score:
        return "strict"
    if broad_score > strict_score:
        return "broad"
    return "medium"


def _build_adaptive_fts_query(query: str) -> tuple:
    """Build an FTS5 query and confidence-threshold delta based on query strictness.

    Returns:
        (fts_query: str, strictness: str, confidence_delta: float)

    Strictness effects:
        'strict' — exact token match (no trailing ``*``); confidence += 0.2.
                   Callers should fall back to prefix match when 0 results returned.
        'medium' — prefix match ``"term"*`` (current default); delta = 0.0.
        'broad'  — OR-conjunction prefix match for higher recall; confidence -= 0.2.
                   Common stopwords stripped from the OR terms to reduce noise.

    The confidence_delta is intended to be added to the caller's min_confidence
    (clamped to [0.0, 1.0]) so adaptive logic is non-breaking to existing contracts.
    """
    strictness = _analyze_query_strictness(query)
    base = _sanitize_fts_query(query)

    if base == '""':
        return base, strictness, 0.0

    terms = base.split()  # ["\"term\"*", ...]

    if strictness == "strict":
        # Strip trailing * to require exact token, not prefix
        fts_query = " ".join(t.rstrip("*") for t in terms)
        confidence_delta = 0.2
    elif strictness == "broad" and len(terms) > 1:
        # OR-conjunction: any term match is sufficient (recall over precision)
        _BROAD_STOPWORDS = frozenset({"the", "for", "and", "with", "that", "this",
                                      "when", "how", "what", "why", "should", "use",
                                      "using", "from", "into", "over", "not", "does",
                                      "have", "are", "was", "we", "our", "they",
                                      "them", "it", "its", "by", "as", "at", "an",
                                      "a", "is", "in", "on", "to", "be", "or",
                                      "do", "so", "if"})
        content_terms = [t for t in terms
                         if t.strip('"*').lower() not in _BROAD_STOPWORDS]
        fts_query = " OR ".join(content_terms if content_terms else terms)
        confidence_delta = -0.2
    else:
        fts_query = base
        confidence_delta = 0.0

    return fts_query, strictness, confidence_delta


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

    # Fallback: substring LIKE search when FTS returns nothing
    if not results:
        like_sql = """
            SELECT d.title, s.section_name, d.doc_type, d.session_id,
                   d.id as document_id,
                   SUBSTR(s.content, MAX(1, INSTR(LOWER(s.content), LOWER(?)) - 40), 128) as excerpt,
                   d.file_path, d.size_bytes,
                   COALESCE(d.source, 'copilot') as doc_source
            FROM sections s
            JOIN documents d ON s.document_id = d.id
            WHERE LOWER(s.content) LIKE ?
        """
        like_params = [query, f"%{query.lower()}%"]
        if doc_type:
            like_sql += " AND d.doc_type = ?"
            like_params.append(doc_type)
        if source_filter and source_filter != "all":
            like_sql += " AND COALESCE(d.source, 'copilot') = ?"
            like_params.append(source_filter)
        like_sql += f" LIMIT {limit}"
        try:
            results = db.execute(like_sql, like_params).fetchall()
            if results:
                print(f"{DIM}(FTS returned 0 — showing substring matches){RESET}")
        except sqlite3.OperationalError:
            pass

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
                   source_filter: str = None, verbose: bool = False,
                   compact: bool = False):
    """Show extracted knowledge entries by category."""
    db = get_db()

    sql = """
        SELECT ke.id, ke.category, ke.title, ke.content, ke.tags,
               ke.confidence, ke.session_id, ke.occurrence_count,
               ke.est_tokens, COALESCE(ke.source, 'copilot') as source
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
        if export_fmt == "json":
            _export_json([])
        else:
            print(f"No knowledge entries found. Run 'python extract-knowledge.py' first.")
        db.close()
        return

    if not rows:
        if export_fmt == "json":
            _export_json([])
        else:
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
        tok = f" ~{r['est_tokens']}tok" if r["est_tokens"] else ""

        if compact:
            print(f"  {DIM}#{r['id']:>4d}{RESET} [{r['category']}] {r['title'][:70]}"
                  f"{DIM}{tok}{RESET}")
        elif verbose:
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
            # Default: one-line title + first-line preview
            first_line = r["content"].split("\n")[0][:80] if r["content"] else ""
            print(f"  {DIM}#{r['id']:>4d}{RESET} {BOLD}{r['title'][:60]}{RESET} "
                  f"{DIM}[conf:{conf}{count}]{RESET}")
            if first_line:
                print(f"        {DIM}{first_line}{RESET}")

    if not compact and not verbose:
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
    """Search knowledge entries with FTS5 and adaptive strictness."""
    db = get_db()

    fts_query, strictness, _ = _build_adaptive_fts_query(query)

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
        rows = []

    # Strict fallback: exact-match returned nothing → retry with prefix query
    if not rows and strictness == "strict":
        base_query = _sanitize_fts_query(query)
        try:
            rows = db.execute("""
                SELECT ke.*, snippet(ke_fts, 1, '>>>', '<<<', '...', 48) as excerpt
                FROM ke_fts fts
                JOIN knowledge_entries ke ON fts.rowid = ke.id
                WHERE ke_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (base_query, limit)).fetchall()
        except sqlite3.OperationalError:
            rows = []

    # Fallback: substring LIKE search when FTS returns nothing
    if not rows:
        try:
            rows = db.execute("""
                SELECT ke.*,
                       SUBSTR(ke.content, MAX(1, INSTR(LOWER(ke.content), LOWER(?)) - 40), 128) as excerpt
                FROM knowledge_entries ke
                WHERE LOWER(ke.title) LIKE ? OR LOWER(ke.content) LIKE ?
                ORDER BY ke.confidence DESC
                LIMIT ?
            """, (query, f"%{query.lower()}%", f"%{query.lower()}%", limit)).fetchall()
            if rows:
                print(f"{DIM}(FTS returned 0 — showing substring matches){RESET}")
        except sqlite3.OperationalError:
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
    """Export results as JSON to stdout.

    Deserializes JSON-encoded column fields (affected_files, facts) so consumers
    receive proper arrays rather than raw JSON strings.
    """
    import json
    import io
    _JSON_ARRAY_FIELDS = ("affected_files", "facts")
    clean = []
    for r in rows:
        d = dict(r)
        d.pop("excerpt", None)
        for field in _JSON_ARRAY_FIELDS:
            if field in d and isinstance(d[field], str):
                try:
                    d[field] = json.loads(d[field])
                except (ValueError, TypeError):
                    pass
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


def query_entity_relations(entity: str):
    """Query entity_relations for a specific entity (subject or object)."""
    db = get_db()
    try:
        rows = db.execute("""
            SELECT subject, predicate, object, noted_at
            FROM entity_relations
            WHERE subject = ? OR object = ?
            ORDER BY noted_at DESC
        """, (entity, entity)).fetchall()
    except sqlite3.OperationalError:
        print("⚠ entity_relations table not found")
        db.close()
        return

    if not rows:
        print(f"No relations found for entity '{entity}'")
        db.close()
        return

    print(f"\n{BOLD}Entity Relations: {entity}{RESET}")
    print("=" * 60)
    for r in rows:
        noted = f" ({r['noted_at'][:10]})" if r['noted_at'] else ""
        print(f"  {r['subject']}  --[{r['predicate']}]-->  {r['object']}{DIM}{noted}{RESET}")
    print(f"\n--- {len(rows)} relations ---")
    db.close()


def list_wings():
    """Show all wings with entry counts."""
    db = get_db()
    rows = db.execute("""
        SELECT wing, COUNT(*) as cnt
        FROM knowledge_entries
        WHERE wing != ''
        GROUP BY wing
        ORDER BY cnt DESC
    """).fetchall()

    if not rows:
        print("No wings found. Use learn.py --wing to categorize entries.")
        db.close()
        return

    print(f"\n{BOLD}Wings{RESET}")
    print("=" * 40)
    for r in rows:
        bar = "█" * min(r['cnt'] // 5, 30)
        print(f"  {r['wing']:<20} {r['cnt']:>4}  {DIM}{bar}{RESET}")
    total = sum(r['cnt'] for r in rows)
    print(f"\n  {'Total':<20} {total:>4}")
    db.close()


def list_rooms(wing: str = ""):
    """Show rooms, optionally filtered by wing."""
    db = get_db()
    if wing:
        rows = db.execute("""
            SELECT room, COUNT(*) as cnt
            FROM knowledge_entries
            WHERE room != '' AND wing = ?
            GROUP BY room
            ORDER BY cnt DESC
        """, (wing,)).fetchall()
    else:
        rows = db.execute("""
            SELECT room, wing, COUNT(*) as cnt
            FROM knowledge_entries
            WHERE room != ''
            GROUP BY room, wing
            ORDER BY cnt DESC
        """).fetchall()

    if not rows:
        msg = f" in wing '{wing}'" if wing else ""
        print(f"No rooms found{msg}.")
        db.close()
        return

    title = f"Rooms in '{wing}'" if wing else "All Rooms"
    print(f"\n{BOLD}{title}{RESET}")
    print("=" * 50)
    for r in rows:
        wing_label = f" [{r['wing']}]" if not wing and 'wing' in r.keys() else ""
        bar = "█" * min(r['cnt'] // 3, 30)
        print(f"  {r['room']:<20}{wing_label:<12} {r['cnt']:>4}  {DIM}{bar}{RESET}")
    db.close()


def show_graph_stats():
    """Show knowledge graph statistics."""
    db = get_db()
    stats = []

    try:
        total = db.execute("SELECT COUNT(*) FROM entity_relations").fetchone()[0]
        stats.append(f"  Relations: {total}")

        predicates = db.execute("""
            SELECT predicate, COUNT(*) as cnt
            FROM entity_relations
            GROUP BY predicate
            ORDER BY cnt DESC
        """).fetchall()
        if predicates:
            stats.append("  Predicates:")
            for p in predicates:
                stats.append(f"    {p['predicate']}: {p['cnt']}")

        subjects = db.execute(
            "SELECT COUNT(DISTINCT subject) FROM entity_relations"
        ).fetchone()[0]
        objects = db.execute(
            "SELECT COUNT(DISTINCT object) FROM entity_relations"
        ).fetchone()[0]
        stats.append(f"  Unique subjects: {subjects}")
        stats.append(f"  Unique objects: {objects}")
    except sqlite3.OperationalError:
        stats.append("  ⚠ entity_relations table not found")

    print(f"\n{BOLD}Knowledge Graph Stats{RESET}")
    print("=" * 40)
    print("\n".join(stats))
    db.close()


def print_usage():
    print(__doc__)


def show_by_file(file_path: str, limit: int = 20, verbose: bool = False,
                 export_fmt: str = None, compact: bool = False):
    """Show knowledge entries that recorded the given file as affected."""
    db = get_db()
    # Use JSON-quoted exact match: files are stored as JSON arrays, so matching
    # '"file_path"' (with double-quotes) ensures we match the full path as an array
    # element rather than a substring of a longer path.  E.g. searching for
    # src/auth.py won't accidentally match tests/src/auth.py or src/auth.py.bak.
    quoted_pattern = f'%"{file_path}"%'
    try:
        rows = db.execute("""
            SELECT id, category, title, content, confidence, affected_files, task_id,
                   est_tokens
            FROM knowledge_entries
            WHERE affected_files LIKE ? AND affected_files != '[]'
            ORDER BY confidence DESC, occurrence_count DESC
            LIMIT ?
        """, (quoted_pattern, limit)).fetchall()
    except sqlite3.OperationalError:
        if export_fmt == "json":
            _export_json([])
        else:
            print("⚠ affected_files column not found. Run build-session-index.py to migrate.")
        db.close()
        return

    if not rows:
        if export_fmt == "json":
            _export_json([])
        else:
            print(f"No knowledge entries recorded for file: {file_path}")
            print(f"Tip: Use 'learn.py --file {file_path}' when recording entries that touch this file.")
        db.close()
        return

    if export_fmt == "json":
        _export_json([dict(r) for r in rows])
        db.close()
        return

    print(f"\n{BOLD}Entries affecting: {file_path} ({len(rows)} results){RESET}\n")
    for r in rows:
        cat_color = {
            "mistake": YELLOW, "pattern": GREEN, "decision": CYAN,
            "tool": MAGENTA, "feature": GREEN, "refactor": DIM,
            "discovery": CYAN,
        }.get(r["category"], "")
        task_note = f"  {DIM}task={r['task_id']}{RESET}" if r["task_id"] else ""
        tok = f" {DIM}~{r['est_tokens']}tok{RESET}" if r["est_tokens"] else ""
        if compact:
            print(f"  {DIM}#{r['id']:>4d}{RESET} [{r['category']}] {r['title'][:70]}{tok}")
        else:
            print(f"  {DIM}#{r['id']:>4d}{RESET} {cat_color}[{r['category']}]{RESET} "
                  f"{BOLD}{r['title'][:65]}{RESET}{task_note}{tok}")
            if verbose and r["content"]:
                first = r["content"].split("\n")[0][:100]
                print(f"         {DIM}{first}{RESET}")
    if not compact and not verbose:
        print(f"\n{DIM}Use --verbose for content preview, --detail <id> for full entry{RESET}")
    db.close()


def show_by_module(module: str, limit: int = 20, verbose: bool = False,
                   export_fmt: str = None, compact: bool = False):
    """Show knowledge entries that affect files in a given module/directory path segment.

    Primary: matches entries whose affected_files contain the module as a path directory
    component (e.g. ``auth`` matches ``auth/session.py`` and ``src/auth/models.py``).
    Fallback: if no file-tagged entries are found, falls back to content/title substring
    search (noisier, clearly labelled).
    """
    db = get_db()
    # Path-segment patterns: match module as a directory component.
    # '"%module/%' matches it as the first directory in the JSON-encoded path.
    # '%/%module/%' matches it as a middle directory.
    pat_first = f'%"{module}/%'
    pat_mid = f'%/{module}/%'
    module_lower = module.lower()
    _fallback_used = False
    try:
        rows = db.execute("""
            SELECT id, category, title, content, confidence, affected_files, task_id,
                   est_tokens
            FROM knowledge_entries
            WHERE affected_files != '[]'
              AND (affected_files LIKE ? OR affected_files LIKE ?)
            ORDER BY confidence DESC, occurrence_count DESC
            LIMIT ?
        """, (pat_first, pat_mid, limit)).fetchall()

        # Fallback: content/title search when no file-tagged entries exist.
        if not rows:
            rows = db.execute("""
                SELECT id, category, title, content, confidence, affected_files, task_id,
                       est_tokens
                FROM knowledge_entries
                WHERE LOWER(content) LIKE ? OR LOWER(title) LIKE ?
                ORDER BY confidence DESC, occurrence_count DESC
                LIMIT ?
            """, (f"%{module_lower}%", f"%{module_lower}%", limit)).fetchall()
            _fallback_used = bool(rows)
    except sqlite3.OperationalError:
        if export_fmt == "json":
            _export_json([])
        else:
            print("⚠ affected_files column not found. Run build-session-index.py to migrate.")
        db.close()
        return

    if not rows:
        if export_fmt == "json":
            _export_json([])
        else:
            print(f"No knowledge entries found for module: {module}")
        db.close()
        return

    if export_fmt == "json":
        _export_json([dict(r) for r in rows])
        db.close()
        return

    label = f"{module} (content/title match)" if _fallback_used else module
    print(f"\n{BOLD}Entries for module: {label} ({len(rows)} results){RESET}\n")
    if _fallback_used:
        print(f"{DIM}(no file-tagged entries found; showing content/title matches){RESET}\n")
    for r in rows:
        cat_color = {
            "mistake": YELLOW, "pattern": GREEN, "decision": CYAN,
            "tool": MAGENTA, "feature": GREEN, "refactor": DIM,
            "discovery": CYAN,
        }.get(r["category"], "")
        task_note = f"  {DIM}task={r['task_id']}{RESET}" if r["task_id"] else ""
        tok = f" {DIM}~{r['est_tokens']}tok{RESET}" if r["est_tokens"] else ""
        if compact:
            print(f"  {DIM}#{r['id']:>4d}{RESET} [{r['category']}] {r['title'][:70]}{tok}")
        else:
            print(f"  {DIM}#{r['id']:>4d}{RESET} {cat_color}[{r['category']}]{RESET} "
                  f"{BOLD}{r['title'][:65]}{RESET}{task_note}{tok}")
            if verbose and r["content"]:
                first = r["content"].split("\n")[0][:100]
                print(f"         {DIM}{first}{RESET}")
    if not compact and not verbose:
        print(f"\n{DIM}Use --verbose for content preview, --detail <id> for full entry{RESET}")
    db.close()


def show_by_task(task_id: str, limit: int = 30, verbose: bool = False,
                 export_fmt: str = None, compact: bool = False):
    """Show knowledge entries recorded under a specific task ID."""
    db = get_db()
    safe = task_id.strip()[:200]
    try:
        rows = db.execute("""
            SELECT id, category, title, content, confidence, affected_files, task_id,
                   occurrence_count, last_seen, est_tokens
            FROM knowledge_entries
            WHERE task_id = ?
            ORDER BY confidence DESC, occurrence_count DESC
            LIMIT ?
        """, (safe, limit)).fetchall()
    except sqlite3.OperationalError:
        if export_fmt == "json":
            import json as _json
            print(_json.dumps({"task_id": task_id, "entries": []}, indent=2))
        else:
            print("⚠ task_id column not found. Run build-session-index.py to migrate.")
        db.close()
        return

    if not rows:
        # Fallback: FTS search on the task_id as a query string
        if export_fmt == "json":
            import json as _json
            print(_json.dumps({"task_id": task_id, "entries": []}, indent=2))
        else:
            print(f"No entries directly tagged task_id='{task_id}'.")
            print(f"Try: python query-session.py '{task_id}' for FTS search.")
        db.close()
        return

    if export_fmt == "json":
        import json as _json
        _JSON_ARRAY_FIELDS = ("affected_files", "facts")
        entries = []
        for r in rows:
            d = dict(r)
            d.pop("excerpt", None)
            for field in _JSON_ARRAY_FIELDS:
                if field in d and isinstance(d[field], str):
                    try:
                        d[field] = _json.loads(d[field])
                    except (ValueError, TypeError):
                        pass
            entries.append(d)
        output = _json.dumps(
            {"task_id": task_id, "entries": entries},
            indent=2, ensure_ascii=False, default=str,
        )
        try:
            print(output)
        except UnicodeEncodeError:
            sys.stdout.buffer.write(output.encode("utf-8"))
            sys.stdout.buffer.write(b"\n")
        db.close()
        return

    print(f"\n{BOLD}Task recall: {task_id} ({len(rows)} entries){RESET}\n")
    for r in rows:
        cat_color = {
            "mistake": YELLOW, "pattern": GREEN, "decision": CYAN,
            "tool": MAGENTA, "feature": GREEN, "refactor": DIM,
            "discovery": CYAN,
        }.get(r["category"], "")
        tok = f" {DIM}~{r['est_tokens']}tok{RESET}" if r["est_tokens"] else ""
        if compact:
            print(f"  {DIM}#{r['id']:>4d}{RESET} [{r['category']}] {r['title'][:70]}{tok}")
        else:
            files = ""
            try:
                import json as _json
                fl = _json.loads(r["affected_files"] or "[]")
                if fl:
                    files = f"  {DIM}files: {', '.join(fl[:2])}{RESET}"
            except Exception:
                pass
            print(f"  {DIM}#{r['id']:>4d}{RESET} {cat_color}[{r['category']}]{RESET} "
                  f"{BOLD}{r['title'][:65]}{RESET}{files}{tok}")
            if verbose and r["content"]:
                first = r["content"].split("\n")[0][:100]
                print(f"         {DIM}{first}{RESET}")
    if not compact and not verbose:
        print(f"\n{DIM}Use --verbose for content preview, --detail <id> for full entry{RESET}")
    db.close()


def show_diff_context(limit: int = 20, verbose: bool = False, export_fmt: str = None,
                      compact: bool = False):
    """Surface knowledge entries relevant to the current git diff.

    Reads changed files from `git diff HEAD --name-only`, then queries
    knowledge entries that recorded those files (via affected_files) or
    mention them in content/title.
    """
    import subprocess
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD", "--name-only"],
            capture_output=True, text=True, timeout=10
        )
        changed = [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]
    except Exception as e:
        print(f"⚠ git diff failed: {e}")
        return

    # Also include staged files
    try:
        result2 = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True, text=True, timeout=10
        )
        staged = [f.strip() for f in result2.stdout.strip().splitlines() if f.strip()]
        changed = list(dict.fromkeys(changed + staged))  # dedupe, preserve order
    except Exception:
        pass

    if not changed:
        if export_fmt == "json":
            import json as _json
            print(_json.dumps({"changed_files": [], "entries": []}, indent=2))
        else:
            print("No changed files in current git diff.")
        return

    if export_fmt != "json":
        print(f"\n{BOLD}Diff context — {len(changed)} changed file(s):{RESET}")
        for f in changed[:10]:
            print(f"  {DIM}  {f}{RESET}")
        if len(changed) > 10:
            print(f"  {DIM}  ... and {len(changed) - 10} more{RESET}")
        print()

    db = get_db()
    seen_ids = set()
    all_rows = []

    for file_path in changed[:15]:  # cap at 15 files to avoid noise
        # Use JSON-quoted exact match for affected_files to prevent false positives
        # from paths that share a prefix (e.g. src/auth.py vs tests/src/auth.py).
        # Content/title matches use the full basename (with extension) to avoid
        # stem-based false positives from common words like "main", "test", "app".
        from pathlib import Path as _Path
        basename = _Path(file_path).name
        basename_lower = basename.lower()
        quoted_pattern = f'%"{file_path}"%'
        try:
            rows = db.execute("""
                SELECT id, category, title, content, confidence,
                       affected_files, task_id, occurrence_count, est_tokens
                FROM knowledge_entries
                WHERE (affected_files LIKE ? AND affected_files != '[]')
                   OR LOWER(content) LIKE ?
                   OR LOWER(title) LIKE ?
                ORDER BY confidence DESC
                LIMIT ?
            """, (quoted_pattern, f"%{basename_lower}%",
                  f"%{basename_lower}%", limit)).fetchall()
        except sqlite3.OperationalError:
            if export_fmt == "json":
                import json as _json
                print(_json.dumps({"changed_files": changed, "entries": []}, indent=2))
            else:
                print("⚠ affected_files column not found. Run build-session-index.py to migrate.")
            db.close()
            return

        for r in rows:
            if r["id"] not in seen_ids:
                seen_ids.add(r["id"])
                all_rows.append((file_path, r))

    if not all_rows:
        if export_fmt == "json":
            import json as _json
            print(_json.dumps({"changed_files": changed, "entries": []}, indent=2))
        else:
            print("No knowledge entries found for changed files.")
            print("Tip: Record entries with 'learn.py --file <path>' to build this surface.")
        db.close()
        return

    if export_fmt == "json":
        import json as _json
        _JSON_ARRAY_FIELDS = ("affected_files", "facts")
        entries = []
        for matched_file, r in all_rows:
            d = dict(r)
            d.pop("excerpt", None)
            for field in _JSON_ARRAY_FIELDS:
                if field in d and isinstance(d[field], str):
                    try:
                        d[field] = _json.loads(d[field])
                    except (ValueError, TypeError):
                        pass
            d["matched_by"] = matched_file
            entries.append(d)
        output = _json.dumps(
            {"changed_files": changed, "entries": entries},
            indent=2, ensure_ascii=False, default=str
        )
        try:
            print(output)
        except UnicodeEncodeError:
            sys.stdout.buffer.write(output.encode("utf-8"))
            sys.stdout.buffer.write(b"\n")
        db.close()
        return

    # Group by file for readable output
    by_file: dict = {}
    for file_path, r in all_rows:
        by_file.setdefault(file_path, []).append(r)

    total = sum(len(v) for v in by_file.values())
    print(f"{BOLD}Found {total} relevant knowledge entries{RESET}\n")
    for file_path, rows in by_file.items():
        if not rows:
            continue
        print(f"  {CYAN}{file_path}{RESET}")
        for r in rows[:5]:
            cat_color = {
                "mistake": YELLOW, "pattern": GREEN, "decision": CYAN,
                "tool": MAGENTA, "feature": GREEN, "refactor": DIM,
                "discovery": CYAN,
            }.get(r["category"], "")
            tok = f" {DIM}~{r['est_tokens']}tok{RESET}" if r["est_tokens"] else ""
            if compact:
                print(f"    {DIM}#{r['id']:>4d}{RESET} [{r['category']}] {r['title'][:60]}{tok}")
            else:
                print(f"    {DIM}#{r['id']:>4d}{RESET} {cat_color}[{r['category']}]{RESET} "
                      f"{r['title'][:60]}{tok}")
                if verbose and r["content"]:
                    first = r["content"].split("\n")[0][:90]
                    print(f"           {DIM}{first}{RESET}")
        print()

    if not compact:
        print(f"{DIM}Use --detail <id> for full entry content{RESET}")
    db.close()


def _apply_budget(text: str, budget: int) -> str:
    """Cap text to budget chars, truncating at the last complete line.

    JSON output (leading { or [) is returned as-is — slicing would corrupt
    its structure.  A warning is emitted to stderr instead.
    """
    if len(text) <= budget:
        return text
    if text.lstrip().startswith(("{", "[")):
        print(
            f"[query-session] --budget {budget}: JSON output ({len(text)} chars) "
            "exceeds budget — returning full JSON to preserve structure",
            file=sys.stderr,
        )
        return text
    truncated = text[:budget].rsplit("\n", 1)[0]
    return truncated + f"\n[BUDGET {budget} chars — showing highest-relevance entries only]\n"


def main():
    args = sys.argv[1:]

    if not args or "--help" in args or "-h" in args:
        print_usage()
        return

    # Parse --budget and --compact early; strip them from args before routing.
    budget = 0
    if "--budget" in args:
        idx = args.index("--budget")
        if idx + 1 < len(args) and not args[idx + 1].startswith("--"):
            try:
                budget = int(args[idx + 1])
            except ValueError:
                budget = 3000  # default on non-numeric value
            args = args[:idx] + args[idx + 2:]  # always strip --budget + its value
        else:
            budget = 3000
            args = args[:idx] + args[idx + 1:]

    compact = "--compact" in args

    if budget > 0:
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                _run(args, compact)
        except SystemExit:
            pass  # still emit whatever was captured before the exit
        output = _apply_budget(buf.getvalue(), budget)
        sys.stdout.write(output)
    else:
        _run(args, compact)


def _run(args: list, compact: bool = False):
    """Main dispatch — called by main() with budget/compact already parsed."""
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

    if "--relate" in args:
        idx = args.index("--relate")
        if idx + 1 < len(args):
            query_entity_relations(args[idx + 1])
        else:
            print("Error: --relate requires an entity name")
        return

    if "--wings" in args:
        list_wings()
        return

    if "--rooms" in args:
        idx = args.index("--rooms")
        wing = args[idx + 1] if idx + 1 < len(args) and not args[idx + 1].startswith("--") else ""
        list_rooms(wing)
        return

    if "--graph-stats" in args:
        show_graph_stats()
        return

    # --- New first-class surfaces ---

    # Parse common flags needed by new surfaces (including export_fmt — must be
    # parsed HERE so --file/--module/--task/--diff can honour --export json)
    limit = 10
    if "--limit" in args:
        idx = args.index("--limit")
        limit = int(args[idx + 1]) if idx + 1 < len(args) else 10
    verbose = "--verbose" in args or "-v" in args
    export_fmt = None
    if "--export" in args:
        idx = args.index("--export")
        export_fmt = args[idx + 1] if idx + 1 < len(args) else "json"

    if "--file" in args:
        idx = args.index("--file")
        if idx + 1 < len(args):
            show_by_file(args[idx + 1], limit=limit, verbose=verbose,
                         export_fmt=export_fmt, compact=compact)
        else:
            print("Error: --file requires a file path")
        return

    if "--module" in args:
        idx = args.index("--module")
        if idx + 1 < len(args):
            show_by_module(args[idx + 1], limit=limit, verbose=verbose,
                           export_fmt=export_fmt, compact=compact)
        else:
            print("Error: --module requires a module/directory name")
        return

    if "--task" in args:
        idx = args.index("--task")
        if idx + 1 < len(args):
            show_by_task(args[idx + 1], limit=limit, verbose=verbose,
                         export_fmt=export_fmt, compact=compact)
        else:
            print("Error: --task requires a task ID")
        return

    if "--diff" in args:
        show_diff_context(limit=limit, verbose=verbose, export_fmt=export_fmt,
                          compact=compact)
        return

    # Knowledge category shortcuts (export_fmt already parsed above)

    # limit/verbose already parsed above; re-read for semantic/search paths
    for shortcut, category in [("--mistakes", "mistake"), ("--patterns", "pattern"),
                                ("--decisions", "decision"), ("--tools", "tool")]:
        if shortcut in args:
            show_knowledge(category, limit, export_fmt, source_filter, verbose,
                           compact=compact)
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
        elif args[i] in ("--compact",):
            i += 1  # already consumed by main()
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
