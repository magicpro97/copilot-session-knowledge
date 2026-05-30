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
    python query-session.py "search" --since 2025-01-01       # Only entries seen on/after date
    python query-session.py "search" --days 30                 # Only entries seen in last 30 days
    python query-session.py --why 42                           # Explain why entry #42 was scored
    python query-session.py --why 42 --json                    # --why output as JSON
    python query-session.py --feedback 42 good                 # Mark entry #42 as good (+1)
    python query-session.py --feedback 42 bad                  # Mark entry #42 as bad (-1)
    python query-session.py --feedback 42 neutral              # Mark entry #42 as neutral (0)

Doc types: checkpoint, research, artifact, plan, claude-session
Knowledge categories: mistake, pattern, decision, tool
Sources: copilot, claude, all (default: all)
Semantic search requires: python embed.py --setup && python embed.py --build
"""

import io
import json
import math
import os
import sqlite3
import sys
import textwrap
import time
from contextlib import redirect_stdout
from pathlib import Path

# Fix Windows console encoding for Unicode output
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SESSION_STATE = Path.home() / ".copilot" / "session-state"
DB_PATH = Path(os.environ.get("SK_DB_PATH", str(SESSION_STATE / "knowledge.db"))).expanduser()


def _safe_int_list(values) -> list[int]:
    out = []
    for value in values:
        try:
            iv = int(value)
        except (TypeError, ValueError):
            continue
        out.append(iv)
    return out


def _estimate_tokens(output_chars: int) -> int:
    return int(math.ceil(output_chars / 4)) if output_chars > 0 else 0


def _record_recall_event(
    event_kind: str,
    surface: str,
    raw_query: str,
    rewritten_query: str,
    task_id: str,
    selected_entry_ids: list[int],
    hit_count: int,
    output_chars: int,
    opened_entry_id: int | None = None,
) -> None:
    payload = (
        time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        event_kind,
        "query-session",
        surface,
        "",
        (raw_query or "")[:500],
        (rewritten_query or "")[:500],
        (task_id or "")[:200],
        "[]",
        json.dumps(_safe_int_list(selected_entry_ids), ensure_ascii=False),
        "[]",
        opened_entry_id,
        max(0, int(hit_count or 0)),
        max(0, int(output_chars or 0)),
        _estimate_tokens(max(0, int(output_chars or 0))),
    )
    db = None
    try:
        db = sqlite3.connect(str(DB_PATH))
        db.execute(
            """
            INSERT INTO recall_events (
                created_at, event_kind, tool, surface, mode,
                raw_query, rewritten_query, task_id, files,
                selected_entry_ids, selected_snippet_ids, opened_entry_id,
                hit_count, output_chars, output_est_tokens
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        db.commit()
    except sqlite3.OperationalError:
        pass
    finally:
        if db is not None:
            db.close()


def _run_with_capture(func, *args, **kwargs):
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = func(*args, **kwargs)
    output = buf.getvalue()
    sys.stdout.write(output)
    return output, result


def _coerce_recall_meta(meta, default: dict) -> dict:
    if not isinstance(meta, dict):
        return dict(default)
    merged = dict(default)
    merged.update(meta)
    return merged


# ANSI colors — auto-detect terminal support
def _supports_color() -> bool:
    """Check if terminal supports ANSI colors (cross-platform)."""
    import os
    import sys

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


import re as _re_module

# ── Status-note suppression (issue #377) ──────────────────────────────────────
# Mirrors the same regex in briefing.py so both modules apply universal suppression.
_STATUS_NOTE_RE = _re_module.compile(
    r"""
    ^(?:
        Wave[-\s]?\d+\b            # "Wave14 …" or "Wave-14 …"
        .*?\b(?:verification\s+is\s+complete|phase[-\s\d]+\s+verification\s+is\s+complete)
        |                          # OR
        Wave[-\s]?\d+\b            # "Wave11 planner recommendation (not yet implemented)"
        .*?\(not\s+yet\s+implemented\)
        |                          # OR
        wave\d+[-\w]+\s+completed  # "wave6-pretooluse-deny completed …"
        |                          # OR
        \[rust-wave                # "[rust-wave7-hook-parity] …"
    )
    """,
    _re_module.VERBOSE | _re_module.IGNORECASE,
)

# ── Synonym expansion (issue #371) ────────────────────────────────────────────
_SYNONYM_MAP: dict[str, list[str]] = {
    "auth": ["auth", "authentication", "login", "token"],
    "authentication": ["authentication", "auth", "login", "token"],
    "error": ["error", "bug", "exception", "failure"],
    "bug": ["bug", "error", "issue", "defect"],
    "config": ["config", "configuration", "settings", "env"],
    "configuration": ["configuration", "config", "settings", "env"],
    "db": ["db", "database", "sqlite", "sql"],
    "database": ["database", "db", "sqlite", "sql"],
    "deploy": ["deploy", "deployment", "release", "publish"],
    "deployment": ["deployment", "deploy", "release", "publish"],
    "test": ["test", "tests", "testing", "spec"],
    "testing": ["testing", "test", "tests", "spec"],
    "perf": ["perf", "performance", "speed", "latency", "slow"],
    "performance": ["performance", "perf", "speed", "latency", "slow"],
    "cache": ["cache", "caching", "redis", "ttl", "invalidation"],
    "caching": ["caching", "cache", "redis", "ttl"],
    "migration": ["migration", "migrate", "schema", "upgrade"],
    "migrate": ["migrate", "migration", "schema", "upgrade"],
    "security": ["security", "vulnerability", "injection", "credential"],
    "search": ["search", "query", "retrieval", "fts", "fulltext"],
    "retrieval": ["retrieval", "search", "query", "recall", "fts"],
    "index": ["index", "indexing", "fts", "fts5"],
    "indexing": ["indexing", "index", "fts", "fts5"],
    "embedding": ["embedding", "vector", "semantic", "similarity"],
    "vector": ["vector", "embedding", "semantic", "similarity"],
    "session": ["session", "sessions", "history", "conversation"],
    "hook": ["hook", "hooks", "preToolUse", "postToolUse", "trigger"],
    "hooks": ["hooks", "hook", "preToolUse", "postToolUse", "trigger"],
    "api": ["api", "endpoint", "route", "rest", "http"],
    "endpoint": ["endpoint", "api", "route", "rest", "http"],
    "log": ["log", "logging", "logs", "output", "stderr"],
    "logging": ["logging", "log", "logs", "output"],
    "sync": ["sync", "synchronize", "push", "pull", "remote"],
    "synchronize": ["synchronize", "sync", "push", "pull", "remote"],
    "skill": ["skill", "skills", "plugin", "extension"],
    "skills": ["skills", "skill", "plugin", "extension"],
    "briefing": ["briefing", "context", "recall", "knowledge"],
    "knowledge": ["knowledge", "briefing", "recall", "learning"],
    "refactor": ["refactor", "refactoring", "rewrite", "cleanup"],
    "refactoring": ["refactoring", "refactor", "rewrite", "cleanup"],
}


def _expand_synonyms(query: str) -> str:
    """Expand query terms using domain synonym map (issue #371).

    Conservative expansion: only 1-6 token queries are expanded.
    Returns a space-joined string of unique expanded terms.
    Use _expand_synonyms_fts for FTS queries that need OR conjunction.
    """
    tokens = query.strip().split()
    if not tokens or len(tokens) > 6:
        return query.strip()

    expanded: list[str] = []
    seen: set[str] = set()
    for tok in tokens:
        clean = tok.lower().strip(" \t\"'.,;!?")
        synonyms = _SYNONYM_MAP.get(clean)
        if synonyms:
            for s in synonyms:
                if s not in seen:
                    expanded.append(s)
                    seen.add(s)
        else:
            if clean not in seen:
                expanded.append(clean)
                seen.add(clean)
    return " ".join(expanded) if expanded else query.strip()


def _expand_synonyms_fts(query: str) -> str:
    """Build an FTS5 OR query from synonym-expanded terms (issue #371).

    Unlike _expand_synonyms which returns space-joined terms (AND semantics
    in FTS5), this function builds an explicit OR-conjunction query so that
    any of the expanded synonyms triggers a match.  Safe to pass directly to
    a ``WHERE ke_fts MATCH ?`` parameter.

    Bypasses _sanitize_fts_query intentionally since it strips OR operators.
    """
    expanded = _expand_synonyms(query)
    # Strip reserved FTS5 operators that would break the MATCH expression
    _STRIP_OPS = frozenset({"OR", "AND", "NOT", "NEAR"})
    terms = [t for t in expanded.split() if t and t.upper() not in _STRIP_OPS]
    # Remove FTS5 special chars per-term
    _FTS_SPECIAL = set('"*(){}:^')
    clean_terms = ["".join(c for c in t if c not in _FTS_SPECIAL) for t in terms]
    clean_terms = [t for t in clean_terms if t]
    if not clean_terms:
        return '""'
    # Return OR-joined prefix query: any synonym triggers a hit
    return " OR ".join(f'"{t}"*' for t in clean_terms)


def get_db() -> sqlite3.Connection:
    """Connect to the knowledge database."""
    if not DB_PATH.exists():
        print(f"Error: Knowledge database not found at {DB_PATH}")
        print("Run 'python build-session-index.py' first to build the index.")
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
    _tech = _re.compile(r"\.[a-z]{1,5}(?:\b|$)|[/\\]|\d{4,}|_[a-z]")
    if any(_tech.search(w) for w in words):
        strict_score += 2

    avg_len = sum(len(w) for w in words) / wc
    if avg_len >= 7:
        strict_score += 1
    elif avg_len <= 3.5:
        broad_score += 1

    # Natural-language stopwords → query reads like a sentence → broad recall
    _STOPWORDS = frozenset(
        {
            "the",
            "for",
            "and",
            "with",
            "that",
            "this",
            "when",
            "how",
            "what",
            "why",
            "should",
            "use",
            "using",
            "from",
            "into",
            "over",
            "not",
            "does",
            "have",
            "are",
            "was",
            "we",
            "our",
            "they",
            "them",
            "it",
            "its",
            "by",
            "as",
            "at",
            "an",
            "a",
            "is",
            "in",
            "on",
            "to",
            "be",
            "or",
            "do",
            "so",
            "if",
        }
    )
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
        _BROAD_STOPWORDS = frozenset(
            {
                "the",
                "for",
                "and",
                "with",
                "that",
                "this",
                "when",
                "how",
                "what",
                "why",
                "should",
                "use",
                "using",
                "from",
                "into",
                "over",
                "not",
                "does",
                "have",
                "are",
                "was",
                "we",
                "our",
                "they",
                "them",
                "it",
                "its",
                "by",
                "as",
                "at",
                "an",
                "a",
                "is",
                "in",
                "on",
                "to",
                "be",
                "or",
                "do",
                "so",
                "if",
            }
        )
        content_terms = [t for t in terms if t.strip('"*').lower() not in _BROAD_STOPWORDS]
        fts_query = " OR ".join(content_terms if content_terms else terms)
        confidence_delta = -0.2
    else:
        fts_query = base
        confidence_delta = 0.0

    return fts_query, strictness, confidence_delta


def _rewrite_query_local(query: str, max_terms: int = 15) -> str:
    """Conservative local query condensation while preserving technical tokens."""
    import re as _re

    if not query.strip():
        return query

    _SHORT_TECH = frozenset(
        {
            "go",
            "db",
            "ui",
            "js",
            "py",
            "io",
            "rx",
            "vm",
            "os",
            "ci",
            "cd",
            "tf",
            "qa",
        }
    )
    _FILLER = frozenset(
        {
            "please",
            "help",
            "me",
            "i",
            "need",
            "to",
            "for",
            "the",
            "a",
            "an",
            "and",
            "or",
            "with",
            "without",
            "that",
            "this",
            "these",
            "those",
            "in",
            "on",
            "at",
            "of",
            "from",
            "by",
            "about",
            "into",
            "it",
            "is",
            "are",
            "be",
            "can",
            "should",
            "would",
            "could",
            "how",
            "what",
            "why",
            "when",
            "where",
            "which",
            "want",
        }
    )

    raw_tokens = query.split()
    condensed = []
    seen = set()

    def _is_technical_token(tok: str) -> bool:
        if not tok:
            return False
        if any(c in tok for c in "/\\._-:#"):
            return True
        if any(c.isdigit() for c in tok):
            return True
        if _re.search(r"[a-z][A-Z]|[A-Z][a-z]", tok):
            return True
        if tok.isupper() and len(tok) > 1:
            return True
        return False

    for tok in raw_tokens:
        clean = tok.strip(" \t\r\n\"'`()[]{}<>.,;!?")
        if not clean:
            continue
        clean_lower = clean.lower()
        keep_short_tech = len(clean) == 2 and clean_lower in _SHORT_TECH
        if not keep_short_tech and clean_lower in _FILLER:
            continue
        keep_exact = _is_technical_token(clean)
        if not keep_exact and not keep_short_tech and len(clean) < 3:
            continue
        out_tok = clean if keep_exact else clean_lower
        if out_tok not in seen:
            condensed.append(out_tok)
            seen.add(out_tok)
        if len(condensed) >= max_terms:
            break

    return " ".join(condensed) if condensed else query.strip()


# ──────────────────────────────────────────────
# Batch C: sessions_fts search helpers
# ──────────────────────────────────────────────

# Column name → (fts5_col_name, snippet_col_index)
# Indices: 0=session_id(UNINDEXED), 1=title, 2=user_messages, 3=assistant_messages, 4=tool_names
# Empirically verified against real DB (see _fts5_empirical.py).
_SESSION_COL_MAP: dict = {
    "user": ("user_messages", 2),
    "assistant": ("assistant_messages", 3),
    "tools": ("tool_names", 4),
    "title": ("title", 1),
}


def _build_column_scoped_query(sanitized_term: str, columns: list) -> str:
    """Build a column-scoped FTS5 query using {col}: terms syntax.

    Syntax empirically verified: {col1 col2}: "term"* works with quoted prefix terms.
    sanitized_term: output of _sanitize_fts_query() — already quoted/prefixed.
    columns: list of FTS5 column names to restrict search to.

    Reuses the _sanitize_fts_query output (C security rule: sanitize before column wrap).
    """
    if not columns:
        return sanitized_term
    col_filter = " ".join(columns)
    return f"{{{col_filter}}}: {sanitized_term}"


def search_sessions_fts(
    query: str,
    *,
    session_id_filter: str | None = None,
    col_name: str | None = None,
    snippet_col: int = 2,
    show_snippet: bool = True,
    limit: int = 10,
    retrieval_query: str | None = None,
) -> list:
    """Search sessions_fts with BM25 ranking and optional column-scoped filter.

    BM25 weights (positional, all 5 columns):
        bm25(sessions_fts, 0, 2.0, 3.0, 1.0, 1.0)
        → session_id=0 (UNINDEXED, ignored), title=2.0, user_messages=3.0,
          assistant_messages=1.0, tool_names=1.0
    Weights verified empirically against real DB.

    Returns list of dicts tagged origin='session'.
    """
    db = get_db()

    query_for_retrieval = retrieval_query if retrieval_query is not None else query
    raw = _sanitize_fts_query(query_for_retrieval)
    if col_name:
        fts_query = _build_column_scoped_query(raw, [col_name])
    else:
        fts_query = raw

    if show_snippet:
        snippet_expr = f"snippet(sessions_fts, {snippet_col}, '<mark>', '</mark>', '\u2026', 12)"
    else:
        snippet_expr = "''"

    sql = (
        f"SELECT session_id, title,"
        f" bm25(sessions_fts, 0, 2.0, 3.0, 1.0, 1.0) AS score,"
        f" {snippet_expr} AS excerpt"
        f" FROM sessions_fts WHERE sessions_fts MATCH ?"
    )
    params: list = [fts_query]

    if session_id_filter:
        sql += " AND session_id = ?"
        params.append(session_id_filter)

    sql += " ORDER BY score LIMIT ?"
    params.append(limit)

    try:
        rows = db.execute(sql, params).fetchall()
    except Exception as exc:
        print(f"{DIM}(sessions_fts unavailable: {exc}){RESET}", file=sys.stderr)
        db.close()
        return []

    results = []
    for r in rows:
        results.append(
            {
                "origin": "session",
                "session_id": r[0],
                "title": r[1],
                "score": r[2],
                "excerpt": r[3] or "",
            }
        )
    db.close()
    return results


def show_session_raw(session_id_prefix: str):
    """Dump raw session content from documents for a given session ID prefix."""
    db = get_db()
    row = db.execute("SELECT id, summary FROM sessions WHERE id LIKE ?", (f"{session_id_prefix}%",)).fetchone()
    if not row:
        print(f"No session found matching: {session_id_prefix}")
        db.close()
        return

    full_id = row[0]
    summary = row[1] or "(no summary)"
    print(f"\n{BOLD}Session raw content: {full_id[:8]}...{RESET}")
    print(f"{DIM}Summary: {summary[:100]}{RESET}\n")

    docs = db.execute(
        """SELECT d.doc_type, d.title, s.section_name, s.content
           FROM documents d
           JOIN sections s ON s.document_id = d.id
           WHERE d.session_id = ?
           ORDER BY d.doc_type, d.seq, s.id""",
        (full_id,),
    ).fetchall()

    if not docs:
        print(f"No documents indexed for session {full_id[:8]}...")
    else:
        for doc in docs:
            print(f"--- {doc[0]}: {doc[1]} [{doc[2]}] ---")
            print(doc[3][:2000])
            if len(doc[3]) > 2000:
                print(f"{DIM}... ({len(doc[3])} chars total){RESET}")
            print()
    db.close()


def search(
    query: str,
    doc_type: str = None,
    limit: int = 10,
    verbose: bool = False,
    source_filter: str = None,
    session_id_filter: str = None,
    retrieval_query: str = None,
):
    """Full-text search across all indexed content."""
    db = get_db()
    query_for_retrieval = retrieval_query if retrieval_query is not None else query

    # Build FTS5 query - sanitize and wrap terms for prefix matching
    fts_query = _sanitize_fts_query(query_for_retrieval)

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

    if session_id_filter:
        sql += " AND fts.session_id = ?"
        params.append(session_id_filter)

    sql += f" ORDER BY rank LIMIT {limit}"

    try:
        results = db.execute(sql, params).fetchall()
    except sqlite3.OperationalError as e:
        print(f"Search error: {e}")
        print("Try simpler search terms or use quotes for exact phrases.")
        db.close()
        return {"hit_count": 0, "selected_entry_ids": []}

    # Fallback: substring LIKE search when FTS returns nothing
    if not results:
        like_query_text = query.strip() or query_for_retrieval
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
        like_params = [like_query_text, f"%{like_query_text.lower()}%"]
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
        print("Tip: Try broader terms or check with --list for available sessions.")
        db.close()
        return {"hit_count": 0, "selected_entry_ids": []}

    print(f"\n{BOLD}Found {len(results)} result(s) for: {query}{RESET}\n")

    for i, r in enumerate(results, 1):
        sid = r["session_id"][:8]
        doc_source = r["doc_source"] if "doc_source" in r.keys() else "copilot"
        type_color = {
            "checkpoint": CYAN,
            "research": GREEN,
            "artifact": YELLOW,
            "plan": MAGENTA,
            "claude-session": CYAN,
        }.get(r["doc_type"], "")
        source_badge = f" {DIM}[{doc_source}]{RESET}" if doc_source != "copilot" else ""

        if verbose:
            print(f"{BOLD}{i}. {r['title']}{RESET}{source_badge}")
            print(
                f"   {DIM}Session:{RESET} {sid}...  "
                f"{type_color}{r['doc_type']}{RESET}  "
                f"{DIM}Section:{RESET} {r['section_name']}  "
                f"{DIM}Size:{RESET} {r['size_bytes'] // 1024}KB"
            )

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
            print(f"  {BOLD}{i}.{RESET} {r['title'][:55]} {type_color}{r['doc_type']}{RESET}{source_badge}")
            print(f"     {DIM}{short}{RESET}")

    if not verbose:
        print(f"\n{DIM}Use --verbose for full excerpts{RESET}")

    db.close()
    return {"hit_count": len(results), "selected_entry_ids": []}


def list_sessions(source_filter: str = None):
    """List all indexed sessions."""
    db = get_db()

    print(f"\n{BOLD}Indexed Sessions{RESET}")
    if source_filter and source_filter != "all":
        print(f"{DIM}(filtered: source={source_filter}){RESET}")
    print()
    print(f"{'ID':10s} {'Src':>7s} {'CP':>3s} {'Res':>4s} {'Files':>5s} {'Plan':>4s}  Summary")
    print(f"{'-' * 10} {'-' * 7} {'-' * 3} {'-' * 4} {'-' * 5} {'-' * 4}  {'-' * 50}")

    sql = """
        SELECT id, total_checkpoints, total_research, total_files, has_plan,
               SUBSTR(summary, 1, 80) as summary,
               COALESCE(source, 'copilot') as source,
               COALESCE(label, '') as label
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
        label_suffix = f"  [{row['label']}]" if row["label"] else ""
        print(
            f"{sid:10s} {src:>7s} {row['total_checkpoints']:3d} {row['total_research']:4d} "
            f"{row['total_files']:5d} {plan:>4s}  {summary}{label_suffix}"
        )

    total = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    docs = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    print(f"\n{DIM}Total: {total} sessions, {docs} documents{RESET}")
    db.close()


def show_session(session_prefix: str):
    """Show details for a specific session."""
    db = get_db()

    row = db.execute("SELECT * FROM sessions WHERE id LIKE ?", (f"{session_prefix}%",)).fetchone()

    if not row:
        print(f"No session found matching: {session_prefix}")
        db.close()
        return

    print(f"\n{BOLD}Session: {row['id']}{RESET}")
    print(f"Path: {row['path']}")
    print(
        f"Checkpoints: {row['total_checkpoints']}  Research: {row['total_research']}  "
        f"Files: {row['total_files']}  Plan: {'Yes' if row['has_plan'] else 'No'}"
    )
    print(f"\n{BOLD}Summary:{RESET}")
    print(textwrap.fill(row["summary"] or "(no summary)", width=90, initial_indent="  ", subsequent_indent="  "))

    print(f"\n{BOLD}Documents:{RESET}")
    for doc in db.execute(
        """
        SELECT doc_type, seq, title, size_bytes
        FROM documents WHERE session_id = ?
        ORDER BY doc_type, seq
    """,
        (row["id"],),
    ):
        type_color = {"checkpoint": CYAN, "research": GREEN, "artifact": YELLOW, "plan": MAGENTA}.get(
            doc["doc_type"], ""
        )
        seq = f"#{doc['seq']:02d}" if doc["seq"] > 0 else "   "
        print(f"  {type_color}{doc['doc_type']:12s}{RESET} {seq} {doc['title']} ({doc['size_bytes'] // 1024}KB)")

    db.close()


def set_session_label(prefix: str, label: str) -> None:
    """Set (or clear) a label on a session matched by ID prefix."""
    db = get_db()
    row = db.execute("SELECT id FROM sessions WHERE id LIKE ?", (f"{prefix}%",)).fetchone()
    if not row:
        print(f"No session found matching prefix: {prefix}")
        db.close()
        return
    db.execute("UPDATE sessions SET label = ? WHERE id = ?", (label.strip(), row["id"]))
    db.commit()
    action = "cleared" if not label.strip() else f"set to {label.strip()!r}"
    print(f"Session {row['id'][:12]}.. label {action}")
    db.close()


def get_session_label(prefix: str) -> None:
    """Show the label for a session matched by ID prefix."""
    db = get_db()
    row = db.execute("SELECT id, COALESCE(label,'') as label FROM sessions WHERE id LIKE ?", (f"{prefix}%",)).fetchone()
    if not row:
        print(f"No session found matching prefix: {prefix}")
        db.close()
        return
    label = row["label"] or "(no label)"
    print(f"Session {row['id'][:12]}..  label: {label}")
    db.close()


def list_session_labels() -> None:
    """List all sessions that have a non-empty label."""
    db = get_db()
    rows = db.execute(
        "SELECT id, COALESCE(label,'') as label, COALESCE(source,'copilot') as source, indexed_at"
        " FROM sessions WHERE COALESCE(label,'') != '' ORDER BY label, indexed_at DESC"
    ).fetchall()
    if not rows:
        print("No labeled sessions found.")
        db.close()
        return
    print(f"\n{BOLD}Labeled Sessions{RESET}\n")
    print(f"{'ID':12s} {'Label':30s} {'Src':>7s}  Indexed")
    print(f"{'-'*12} {'-'*30} {'-'*7}  {'-'*20}")
    for row in rows:
        sid = row["id"][:10] + ".."
        label = (row["label"] or "")[:30]
        src = (row["source"] or "copilot")[:7]
        indexed = (row["indexed_at"] or "")[:19]
        print(f"{sid:12s} {label:30s} {src:>7s}  {indexed}")
    print(f"\n{DIM}Total: {len(rows)} labeled session(s){RESET}")
    db.close()


def cmd_digest(prefix: str, as_json: bool = False) -> int:
    """Show a digest of a session matched by ID prefix.

    Outputs four sections: header, knowledge added, files touched, checkpoints.
    Returns 0 on success, 1 if no session matches.
    """
    import json as _json_dig

    db = get_db()
    row = db.execute("SELECT * FROM sessions WHERE id LIKE ?||'%'", (prefix,)).fetchone()
    if not row:
        print(f"No session found matching '{prefix}'")
        db.close()
        return 1

    session_id = row["id"]

    # Detect optional columns added by later migrations.
    session_cols = {r[1] for r in db.execute("PRAGMA table_info(sessions)").fetchall()}
    label = row["label"] if "label" in session_cols else ""
    cost = row["cost_usd_est"] if "cost_usd_est" in session_cols else None
    created_at = (row["indexed_at"] or "")[:19]

    # Knowledge entries
    ke_rows = db.execute(
        "SELECT title, category FROM knowledge_entries WHERE session_id = ? ORDER BY id",
        (session_id,),
    ).fetchall()

    # Files touched (table may not exist yet)
    sf_rows = []
    try:
        sf_rows = db.execute(
            "SELECT file_path, tool_name FROM session_files WHERE session_id = ?",
            (session_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        pass

    # Checkpoints (table may not exist; fall back to documents)
    cp_rows = []
    try:
        cp_rows = db.execute(
            "SELECT checkpoint_number, title FROM checkpoints WHERE session_id = ? ORDER BY checkpoint_number",
            (session_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        try:
            cp_rows = db.execute(
                "SELECT seq AS checkpoint_number, title FROM documents"
                " WHERE session_id = ? AND doc_type = 'checkpoint' ORDER BY seq",
                (session_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            pass

    db.close()

    if as_json:
        result = {
            "id": session_id,
            "summary": (row["summary"] or "").strip(),
            "label": label,
            "created_at": created_at,
            "cost_usd_est": cost,
            "knowledge": [{"title": r["title"], "category": r["category"]} for r in ke_rows],
            "files": [{"file_path": r["file_path"], "tool_name": r["tool_name"]} for r in sf_rows],
            "checkpoints": [
                {"checkpoint_number": r["checkpoint_number"], "title": r["title"]} for r in cp_rows
            ],
        }
        print(_json_dig.dumps(result, indent=2))
        return 0

    print(f"\n{BOLD}Session: {session_id}{RESET}")
    print(f"Summary: {(row['summary'] or '').strip()[:200]}")
    if label:
        print(f"Label:   {label}")
    print(f"Created: {created_at}")
    if cost is not None:
        print(f"Cost:    ${cost:.4f}")

    print(f"\n{BOLD}Knowledge added ({len(ke_rows)}){RESET}")
    if ke_rows:
        for r in ke_rows:
            print(f"  [{r['category']:12s}] {r['title']}")
    else:
        print("  (none)")

    print(f"\n{BOLD}Files touched ({len(sf_rows)}){RESET}")
    if sf_rows:
        for r in sf_rows:
            print(f"  {(r['tool_name'] or ''):10s} {r['file_path']}")
    else:
        print("  (none)")

    print(f"\n{BOLD}Checkpoints ({len(cp_rows)}){RESET}")
    if cp_rows:
        for r in cp_rows:
            print(f"  #{r['checkpoint_number']:02d} {r['title']}")
    else:
        print("  (none)")

    return 0


def cmd_stats(args: list) -> int:
    """Show session stats grouped by a chosen dimension.

    Args supported: --by label|branch|day|week  --since DAYS  --limit N  --json
    Returns 0 always (empty result is not an error).
    """
    import datetime as _dt_stats
    import json as _json_stats

    by = "day"
    since_days = 30
    limit = 20
    as_json = "--json" in args

    i = 0
    while i < len(args):
        if args[i] == "--by" and i + 1 < len(args):
            by = args[i + 1]
            i += 2
        elif args[i] == "--since" and i + 1 < len(args):
            try:
                since_days = int(args[i + 1])
            except ValueError:
                pass
            i += 2
        elif args[i] == "--limit" and i + 1 < len(args):
            try:
                limit = int(args[i + 1])
            except ValueError:
                pass
            i += 2
        else:
            i += 1

    valid_by = ("label", "branch", "day", "week")
    if by not in valid_by:
        print(f"Error: --by must be one of {', '.join(valid_by)}", file=sys.stderr)
        return 1

    since_date = (
        _dt_stats.datetime.now(_dt_stats.timezone.utc) - _dt_stats.timedelta(days=since_days)
    ).strftime("%Y-%m-%d")

    db = get_db()
    session_cols = {r[1] for r in db.execute("PRAGMA table_info(sessions)").fetchall()}
    has_cost = "cost_usd_est" in session_cols
    has_label = "label" in session_cols
    has_branch = "branch" in session_cols

    if by == "label":
        dim_expr = "COALESCE(s.label, '')" if has_label else "''"
    elif by == "branch":
        dim_expr = "COALESCE(s.branch, '')" if has_branch else "''"
    elif by == "week":
        dim_expr = "strftime('%Y-W%W', s.indexed_at)"
    else:  # day
        dim_expr = "substr(s.indexed_at, 1, 10)"

    cost_expr = "COALESCE(SUM(s.cost_usd_est), 0.0)" if has_cost else "0.0"

    sql = (
        "SELECT " + dim_expr + " AS dimension,"
        " COUNT(DISTINCT s.id) AS sessions,"
        " COUNT(ke.id) AS entries,"
        " " + cost_expr + " AS total_cost,"
        " MAX(s.indexed_at) AS last_active"
        " FROM sessions s"
        " LEFT JOIN knowledge_entries ke ON ke.session_id = s.id"
        " WHERE s.indexed_at >= ?"
        " GROUP BY " + dim_expr +
        " ORDER BY last_active DESC"
        " LIMIT ?"
    )

    rows = db.execute(sql, (since_date, limit)).fetchall()
    db.close()

    if as_json:
        result = [
            {
                "dimension": r["dimension"],
                "sessions": r["sessions"],
                "entries": r["entries"],
                "total_cost": r["total_cost"],
                "last_active": r["last_active"],
            }
            for r in rows
        ]
        print(_json_stats.dumps(result, indent=2))
        return 0

    if not rows:
        print(f"No sessions found in the last {since_days} days.")
        return 0

    print(f"\n{BOLD}Session Stats — by {by} (last {since_days} days){RESET}\n")
    print(f"{'Dimension':25s} {'Sessions':>8s} {'Entries':>8s} {'Cost':>9s}  Last Active")
    print(f"{'-'*25} {'-'*8} {'-'*8} {'-'*9}  {'-'*19}")
    for r in rows:
        dim = (r["dimension"] or "(none)")[:25]
        cost_str = f"${r['total_cost']:.4f}" if r["total_cost"] else "$0.0000"
        last = (r["last_active"] or "")[:19]
        print(f"{dim:25s} {r['sessions']:>8d} {r['entries']:>8d} {cost_str:>9s}  {last}")
    print(f"\n{DIM}Total: {len(rows)} row(s){RESET}")
    return 0


def show_recent(limit: int = 10):
    """Show recently indexed documents."""
    db = get_db()

    print(f"\n{BOLD}Recently Indexed Documents{RESET}\n")

    for doc in db.execute(
        """
        SELECT d.*, s.id as session_id
        FROM documents d
        JOIN sessions s ON d.session_id = s.id
        ORDER BY d.indexed_at DESC
        LIMIT ?
    """,
        (limit,),
    ):
        sid = doc["session_id"][:8]
        type_color = {"checkpoint": CYAN, "research": GREEN, "artifact": YELLOW, "plan": MAGENTA}.get(
            doc["doc_type"], ""
        )
        print(f"  {type_color}{doc['doc_type']:12s}{RESET} {sid}.. {doc['title']} ({doc['size_bytes'] // 1024}KB)")

    db.close()


def show_knowledge(
    category: str,
    limit: int = 20,
    export_fmt: str = None,
    source_filter: str = None,
    verbose: bool = False,
    compact: bool = False,
):
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
            print("No knowledge entries found. Run 'python extract-knowledge.py' first.")
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
            print(f"  {DIM}#{r['id']:>4d}{RESET} [{r['category']}] {r['title'][:70]}{DIM}{tok}{RESET}")
        elif verbose:
            tags = f" [{r['tags']}]" if r["tags"] else ""
            print(f"{BOLD}{i}. {r['title']}{RESET}")
            print(
                f"   {DIM}ID:{RESET} #{r['id']}  "
                f"{DIM}Session:{RESET} {sid}..  "
                f"{DIM}Confidence:{RESET} {conf}{count}  "
                f"{DIM}Tags:{RESET}{tags}"
            )
            content_preview = r["content"][:300].replace("\n", "\n   ")
            print(f"   {content_preview}")
            if len(r["content"]) > 300:
                print(f"   {DIM}... ({len(r['content'])} chars total){RESET}")
            print()
        else:
            # Default: one-line title + first-line preview
            first_line = r["content"].split("\n")[0][:80] if r["content"] else ""
            print(f"  {DIM}#{r['id']:>4d}{RESET} {BOLD}{r['title'][:60]}{RESET} {DIM}[conf:{conf}{count}]{RESET}")
            if first_line:
                print(f"        {DIM}{first_line}{RESET}")

    if not compact and not verbose:
        print(f"\n{DIM}Use --detail <id> for full content, --verbose for expanded view{RESET}")

    db.close()


def show_detail(entry_id: int):
    """Show full detail of a specific knowledge entry by ID."""
    db = get_db()
    row = db.execute(
        """
        SELECT ke.*, COALESCE(ke.source, 'copilot') as src,
               d.title as doc_title, d.doc_type,
               d.file_path as doc_file_path, d.file_path,
               d.seq as doc_seq
        FROM knowledge_entries ke
        LEFT JOIN documents d ON ke.document_id = d.id
        WHERE ke.id = ?
    """,
        (entry_id,),
    ).fetchone()

    if not row:
        print(f"No knowledge entry with ID {entry_id}")
        db.close()
        return {"opened_entry_id": int(entry_id), "hit_count": 0, "selected_entry_ids": []}

    print(f"\n{BOLD}Knowledge Entry #{entry_id}{RESET}")
    print(f"{'=' * 60}")
    print(f"{BOLD}Category:{RESET} {row['category'].upper()}")
    print(f"{BOLD}Title:{RESET} {row['title']}")
    print(f"{BOLD}Source:{RESET} {row['src']}")
    print(f"{BOLD}Session:{RESET} {row['session_id'][:12]}...")
    print(f"{BOLD}Confidence:{RESET} {row['confidence']:.2f}  {DIM}Occurrences:{RESET} {row['occurrence_count']}")
    if row["tags"]:
        print(f"{BOLD}Tags:{RESET} {row['tags']}")
    row_dict = dict(row)
    if row["doc_title"]:
        print(f"{BOLD}Document:{RESET} {row['doc_title']} ({row['doc_type']})")
    source_label = _source_label_from_row(row_dict)
    if source_label:
        print(f"{BOLD}Provenance:{RESET} {source_label}")
    location_label = _code_location_label_from_row(row_dict)
    snippet_freshness = _compute_snippet_freshness(row_dict)
    code_snippet = (row_dict.get("code_snippet") or "").strip()
    if location_label:
        print(f"{BOLD}Code location:{RESET} {location_label}")
        print(f"{BOLD}Snippet freshness:{RESET} {snippet_freshness}")
    else:
        print(f"{BOLD}Snippet freshness:{RESET} {snippet_freshness}")
    if row["first_seen"]:
        print(f"{BOLD}First seen:{RESET} {row['first_seen']}")
    if row["last_seen"]:
        print(f"{BOLD}Last seen:{RESET} {row['last_seen']}")

    print(f"\n{BOLD}Content:{RESET}")
    print(f"{'-' * 60}")
    print(row["content"])
    print(f"{'-' * 60}")

    if code_snippet:
        code_language = (row_dict.get("code_language") or "").strip()
        snippet_label = "Code snippet"
        if code_language:
            snippet_label = f"{snippet_label} ({code_language})"
        print(f"\n{BOLD}{snippet_label}:{RESET}")
        print(f"{'-' * 60}")
        print(code_snippet)
        print(f"{'-' * 60}")

    # Show SUPERSEDES relations for this entry
    try:
        supersedes_rows = db.execute(
            """
            SELECT ke.id, ke.title, ke.category
            FROM knowledge_relations kr
            JOIN knowledge_entries ke ON kr.target_id = ke.id
            WHERE kr.source_id = ? AND kr.relation_type = 'SUPERSEDES'
            ORDER BY ke.id
            """,
            (entry_id,),
        ).fetchall()
        superseded_by_rows = db.execute(
            """
            SELECT ke.id, ke.title, ke.category
            FROM knowledge_relations kr
            JOIN knowledge_entries ke ON kr.source_id = ke.id
            WHERE kr.target_id = ? AND kr.relation_type = 'SUPERSEDES'
            ORDER BY ke.id
            """,
            (entry_id,),
        ).fetchall()
        if supersedes_rows:
            print(f"\n{BOLD}Supersedes:{RESET}")
            for r in supersedes_rows:
                print(f"  #{r['id']} [{r['category']}] {r['title']}")
        if superseded_by_rows:
            print(f"\n{BOLD}Superseded by:{RESET}")
            for r in superseded_by_rows:
                print(f"  #{r['id']} [{r['category']}] {r['title']}")
    except sqlite3.OperationalError:
        pass  # fail-open: older DB without knowledge_relations or relation_type column

    db.close()
    return {"opened_entry_id": int(entry_id), "hit_count": 1, "selected_entry_ids": [int(entry_id)]}


def explain_why(entry_id: int, as_json: bool = False):
    """Explain why an entry scored as it did: FTS rank, recency decay, recurrence weight, final score.

    Re-computes the same signals used by briefing.py's _recency_composite_score so
    the output is directly comparable to ranking decisions made during briefing.
    """
    import datetime as _dt
    import math as _math

    db = get_db()
    row = db.execute(
        """
        SELECT ke.id, ke.title, ke.category, ke.confidence,
               ke.occurrence_count, ke.last_seen,
               COALESCE(ke.intensity, ke.confidence) as intensity,
               COALESCE(ke.priority, 'P2') as priority
        FROM knowledge_entries ke WHERE ke.id = ?
    """,
        (entry_id,),
    ).fetchone()

    if not row:
        print(f"No knowledge entry with ID {entry_id}")
        db.close()
        return

    row_d = dict(row)

    # ── recency decay (mirrors briefing._recency_decay) ──────────────────────
    _half_life = 30.0
    try:
        _hl_row = db.execute("SELECT value FROM wakeup_config WHERE key='briefing_recency_half_life'").fetchone()
        if _hl_row and _hl_row[0]:
            v = float(_hl_row[0])
            if v > 0:
                _half_life = v
    except Exception:
        pass

    last_seen_str = row_d.get("last_seen")
    recency_decay = 1.0
    age_days = None
    if last_seen_str:
        try:
            ts_str = str(last_seen_str)[:19].replace("T", " ")
            ts = _dt.datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            now_utc = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
            age_days = max(0.0, (now_utc - ts).total_seconds() / 86400.0)
            recency_decay = 0.5 ** (age_days / _half_life)
        except Exception:
            recency_decay = 1.0

    # ── recurrence weight ─────────────────────────────────────────────────────
    occurrence_count = int(row_d.get("occurrence_count") or 1)
    # Logarithmic diminishing returns: same formula used in briefing delivery tracking
    recurrence_weight = 1.0 + _math.log1p(max(0, occurrence_count - 1)) * 0.1

    # ── priority base (mirrors briefing._recency_composite_score) ─────────────
    priority_bases = {"P0": 4.0, "P1": 2.0, "P2": 0.0, "P3": -2.0}
    priority_raw = str(row_d.get("priority") or "P2")
    priority_base = priority_bases.get(priority_raw, 0.0)

    intensity = float(row_d.get("intensity") or row_d.get("confidence") or 0.5)
    final_score = priority_base + intensity * recency_decay

    # ── FTS rank (run a quick self-match) ─────────────────────────────────────
    fts_rank = None
    try:
        fts_row = db.execute(
            """
            SELECT rank FROM ke_fts fts
            JOIN knowledge_entries ke ON fts.rowid = ke.id
            WHERE ke.id = ?
            LIMIT 1
        """,
            (entry_id,),
        ).fetchone()
        if fts_row:
            fts_rank = round(float(fts_row[0]), 6)
    except Exception:
        pass

    db.close()

    result = {
        "entry_id": entry_id,
        "title": row_d["title"],
        "category": row_d["category"],
        "priority": priority_raw,
        "priority_base": priority_base,
        "intensity": round(intensity, 4),
        "confidence": round(float(row_d.get("confidence") or 0.5), 4),
        "recency_decay": round(recency_decay, 6),
        "recency_half_life_days": _half_life,
        "age_days": round(age_days, 2) if age_days is not None else None,
        "last_seen": last_seen_str,
        "occurrence_count": occurrence_count,
        "recurrence_weight": round(recurrence_weight, 6),
        "fts_rank": fts_rank,
        "final_score": round(final_score, 6),
    }

    if as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    print(f"\n{BOLD}Score breakdown for entry #{entry_id}{RESET}")
    print(f"{'=' * 60}")
    print(f"{BOLD}Title:{RESET}             {row_d['title']}")
    print(f"{BOLD}Category:{RESET}          {row_d['category']}")
    print(f"{BOLD}Priority:{RESET}          {priority_raw}  (base={priority_base:+.1f})")
    print(f"{BOLD}Intensity:{RESET}         {intensity:.4f}")
    print(f"{BOLD}Confidence:{RESET}        {float(row_d.get('confidence') or 0.5):.4f}")
    print(f"{BOLD}Last seen:{RESET}         {last_seen_str or '(none)'}")
    age_str = f"{age_days:.1f} days ago" if age_days is not None else "unknown"
    print(f"{BOLD}Age:{RESET}               {age_str}  (half-life={_half_life}d)")
    print(f"{BOLD}Recency decay:{RESET}     {recency_decay:.6f}")
    print(f"{BOLD}Occurrence count:{RESET}  {occurrence_count}")
    print(f"{BOLD}Recurrence weight:{RESET} {recurrence_weight:.6f}")
    fts_str = f"{fts_rank:.6f}" if fts_rank is not None else "n/a (entry not in FTS index)"
    print(f"{BOLD}FTS rank:{RESET}          {fts_str}")
    print(f"{'─' * 60}")
    print(f"{BOLD}Final composite score:{RESET}  {final_score:.6f}")
    print(f"  = priority_base({priority_base:+.1f}) + intensity({intensity:.4f}) × recency_decay({recency_decay:.6f})")


def show_context(entry_id: int):
    """Show a knowledge entry plus related entries from same session/category."""
    db = get_db()
    row = db.execute(
        """
        SELECT ke.*, COALESCE(ke.source, 'copilot') as src
        FROM knowledge_entries ke WHERE ke.id = ?
    """,
        (entry_id,),
    ).fetchone()

    if not row:
        print(f"No knowledge entry with ID {entry_id}")
        db.close()
        return

    # Show the main entry (compact)
    print(f"\n{BOLD}Context for: {row['title']}{RESET}")
    print(f"{DIM}Category: {row['category']} | Session: {row['session_id'][:12]}...{RESET}")
    print(f"\n{row['content'][:500]}")
    if len(row["content"]) > 500:
        print(f"{DIM}... ({len(row['content'])} chars, use --detail {entry_id} for full){RESET}")

    # Find related: same session
    same_session = db.execute(
        """
        SELECT id, category, title, confidence
        FROM knowledge_entries
        WHERE session_id = ? AND id != ?
        ORDER BY confidence DESC LIMIT 10
    """,
        (row["session_id"], entry_id),
    ).fetchall()

    if same_session:
        print(f"\n{BOLD}Same session entries:{RESET}")
        for r in same_session:
            print(f"  {DIM}#{r['id']}{RESET} [{r['category']}] {r['title']} {DIM}(conf: {r['confidence']:.1f}){RESET}")

    # Find related: same category, different session
    same_category = db.execute(
        """
        SELECT id, category, title, confidence, session_id
        FROM knowledge_entries
        WHERE category = ? AND session_id != ? AND id != ?
        ORDER BY confidence DESC LIMIT 5
    """,
        (row["category"], row["session_id"], entry_id),
    ).fetchall()

    if same_category:
        print(f"\n{BOLD}Related {row['category']} entries (other sessions):{RESET}")
        for r in same_category:
            sid = r["session_id"][:8]
            print(f"  {DIM}#{r['id']}{RESET} {r['title']} {DIM}(session: {sid}.. conf: {r['confidence']:.1f}){RESET}")

    # Check knowledge_relations table if it exists
    try:
        relations = db.execute(
            """
            SELECT kr.relation_type, kr.confidence as rel_conf,
                   ke.id as rel_id, ke.category, ke.title
            FROM knowledge_relations kr
            JOIN knowledge_entries ke ON ke.id = CASE
                WHEN kr.source_id = ? THEN kr.target_id
                ELSE kr.source_id END
            WHERE kr.source_id = ? OR kr.target_id = ?
            ORDER BY kr.confidence DESC LIMIT 10
        """,
            (entry_id, entry_id, entry_id),
        ).fetchall()
        if relations:
            print(f"\n{BOLD}Linked entries:{RESET}")
            for r in relations:
                print(
                    f"  {DIM}#{r['rel_id']}{RESET} --{r['relation_type']}--> "
                    f"[{r['category']}] {r['title']} "
                    f"{DIM}(conf: {r['rel_conf']:.1f}){RESET}"
                )
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
        outgoing = db.execute(
            """
            SELECT kr.relation_type, kr.confidence, ke.id, ke.category, ke.title
            FROM knowledge_relations kr
            JOIN knowledge_entries ke ON kr.target_id = ke.id
            WHERE kr.source_id = ?
            ORDER BY kr.confidence DESC
        """,
            (entry_id,),
        ).fetchall()

        incoming = db.execute(
            """
            SELECT kr.relation_type, kr.confidence, ke.id, ke.category, ke.title
            FROM knowledge_relations kr
            JOIN knowledge_entries ke ON kr.source_id = ke.id
            WHERE kr.target_id = ?
            ORDER BY kr.confidence DESC
        """,
            (entry_id,),
        ).fetchall()
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
            print(
                f"  [{r['relation_type']}] {DIM}#{r['id']}{RESET} [{r['category']}] "
                f"{r['title'][:60]} {DIM}(conf: {r['confidence']:.1f}){RESET}"
            )

    if incoming:
        print(f"\n{BOLD}\u2190 Incoming ({len(incoming)}):{RESET}")
        for r in incoming[:15]:
            print(
                f"  [{r['relation_type']}] {DIM}#{r['id']}{RESET} [{r['category']}] "
                f"{r['title'][:60]} {DIM}(conf: {r['confidence']:.1f}){RESET}"
            )

    db.close()


def show_graph(topic: str):
    """Show a mini knowledge graph around a topic."""
    db = get_db()

    fts_query = _sanitize_fts_query(topic)

    try:
        matches = db.execute(
            """
            SELECT ke.id, ke.category, ke.title, ke.confidence
            FROM ke_fts fts
            JOIN knowledge_entries ke ON fts.rowid = ke.id
            WHERE ke_fts MATCH ?
            ORDER BY rank
            LIMIT 5
        """,
            (fts_query,),
        ).fetchall()
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

    entry_ids = [m["id"] for m in matches]

    for m in matches:
        eid = m["id"]
        print(
            f"\n{BOLD}\u25cf #{eid} [{m['category']}] {m['title'][:60]}{RESET} "
            f"{DIM}(conf: {m['confidence']:.1f}){RESET}"
        )

        try:
            relations = db.execute(
                """
                SELECT kr.relation_type, kr.confidence,
                       CASE WHEN kr.source_id = ? THEN kr.target_id ELSE kr.source_id END as other_id,
                       ke.category, ke.title
                FROM knowledge_relations kr
                JOIN knowledge_entries ke ON ke.id = CASE WHEN kr.source_id = ? THEN kr.target_id ELSE kr.source_id END
                WHERE kr.source_id = ? OR kr.target_id = ?
                ORDER BY kr.confidence DESC
                LIMIT 8
            """,
                (eid, eid, eid, eid),
            ).fetchall()

            for r in relations:
                marker = "\u2194" if r["other_id"] in entry_ids else "\u2192"
                print(
                    f"  {marker} [{r['relation_type']}] {DIM}#{r['other_id']}{RESET} "
                    f"[{r['category']}] {r['title'][:50]}"
                )
        except sqlite3.OperationalError:
            print(f"⚠ knowledge_relations table not found; skipping relations for #{eid}", file=sys.stderr)

    try:
        placeholders = ",".join("?" * len(entry_ids))
        total_rels = db.execute(
            f"SELECT COUNT(*) FROM knowledge_relations WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})",
            entry_ids + entry_ids,
        ).fetchone()[0]
    except sqlite3.OperationalError:
        total_rels = 0

    print(f"\n--- {len(matches)} entries, {total_rels} relations ---")
    db.close()


def search_knowledge(
    query: str,
    limit: int = 10,
    export_fmt: str = None,
    retrieval_query: str = None,
    error_type: str = None,
    since_date: "str | None" = None,
):
    """Search knowledge entries with FTS5 and adaptive strictness.

    ``since_date`` is an optional ISO-8601 date string (``YYYY-MM-DD``).  When
    provided, only entries whose ``last_seen >= since_date`` are returned.
    """
    db = get_db()
    query_for_retrieval = retrieval_query if retrieval_query is not None else query

    # If retrieval_query is already a pre-built FTS5 query (contains OR conjunction
    # or explicit prefix wildcards produced by _expand_synonyms_fts), use it directly
    # to avoid _sanitize_fts_query stripping the OR operators and wildcards.
    _is_prebuilt_fts = retrieval_query is not None and (" OR " in retrieval_query or ('"*' in retrieval_query))
    if _is_prebuilt_fts:
        fts_query = retrieval_query
        strictness = "medium"
    else:
        fts_query, strictness, _ = _build_adaptive_fts_query(query_for_retrieval)

    # Build optional error_type WHERE clause
    et_clause = ""
    et_params: list = []
    if error_type:
        et_clause = " AND ke.error_type = ?"
        et_params = [error_type]

    # Build optional date-filter clause
    date_clause = ""
    date_params: list = []
    if since_date:
        date_clause = " AND ke.last_seen >= ?"
        date_params = [since_date]

    try:
        rows = db.execute(
            f"""
            SELECT ke.*, snippet(ke_fts, 1, '>>>', '<<<', '...', 48) as excerpt
            FROM ke_fts fts
            JOIN knowledge_entries ke ON fts.rowid = ke.id
            WHERE ke_fts MATCH ?{et_clause}{date_clause}
            ORDER BY rank
            LIMIT ?
        """,
            [fts_query, *et_params, *date_params, limit],
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []

    # Strict fallback: exact-match returned nothing → retry with prefix query
    if not rows and strictness == "strict":
        base_query = _sanitize_fts_query(query_for_retrieval)
        try:
            rows = db.execute(
                f"""
                SELECT ke.*, snippet(ke_fts, 1, '>>>', '<<<', '...', 48) as excerpt
                FROM ke_fts fts
                JOIN knowledge_entries ke ON fts.rowid = ke.id
                WHERE ke_fts MATCH ?{et_clause}{date_clause}
                ORDER BY rank
                LIMIT ?
            """,
                [base_query, *et_params, *date_params, limit],
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []

    # Fallback: substring LIKE search when FTS returns nothing
    if not rows:
        like_query_text = query.strip() or query_for_retrieval
        try:
            et_like = " AND ke.error_type = ?" if error_type else ""
            et_like_params = [error_type] if error_type else []
            date_like = " AND ke.last_seen >= ?" if since_date else ""
            date_like_params = [since_date] if since_date else []
            rows = db.execute(
                f"""
                SELECT ke.*,
                       SUBSTR(ke.content, MAX(1, INSTR(LOWER(ke.content), LOWER(?)) - 40), 128) as excerpt
                FROM knowledge_entries ke
                WHERE (LOWER(ke.title) LIKE ? OR LOWER(ke.content) LIKE ?){et_like}{date_like}
                ORDER BY ke.confidence DESC
                LIMIT ?
            """,
                [
                    like_query_text,
                    f"%{like_query_text.lower()}%",
                    f"%{like_query_text.lower()}%",
                    *et_like_params,
                    *date_like_params,
                    limit,
                ],
            ).fetchall()
            if rows:
                print(f"{DIM}(FTS returned 0 — showing substring matches){RESET}")
        except sqlite3.OperationalError:
            rows = []

    if export_fmt == "json" and rows:
        # Issue #377: suppress status-note entries in all output formats
        rows = [r for r in rows if not _STATUS_NOTE_RE.search(dict(r).get("title", "") or "")]
        _export_json([dict(r) for r in rows])
        db.close()
        return

    # Issue #377: apply status-note suppression for text output too
    if rows:
        rows = [r for r in rows if not _STATUS_NOTE_RE.search(dict(r).get("title", "") or "")]

    if rows:
        print(f"\n{BOLD}Knowledge entries matching: {query} ({len(rows)} results){RESET}\n")
        for i, r in enumerate(rows, 1):
            sid = r["session_id"][:8]
            excerpt = r["excerpt"].replace(">>>", f"{BOLD}{YELLOW}").replace("<<<", f"{RESET}")
            # Show error lifecycle metadata when available
            meta_parts = []
            err_type = r["error_type"] if "error_type" in r.keys() else None
            severity = r["severity"] if "severity" in r.keys() else None
            root_cause = r["root_cause"] if "root_cause" in r.keys() else None
            if err_type:
                meta_parts.append(f"type:{err_type}")
            if severity:
                meta_parts.append(f"sev:{severity}")
            if root_cause:
                meta_parts.append(f"cause:{root_cause[:40]}")
            meta_str = f" {DIM}({', '.join(meta_parts)}){RESET}" if meta_parts else ""
            print(f"{BOLD}{i}. [{r['category']}] {r['title']}{RESET}{meta_str}")
            print(f"   {DIM}Session:{RESET} {sid}..  {DIM}Tags:{RESET} {r['tags']}")
            print(f"   {excerpt}")
            print()

    if not rows and export_fmt != "json":
        # Issue #380: no-hit insights — help caller understand why search failed
        try:
            total = db.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
            if total == 0:
                print(f"No knowledge entries found for: {query!r}")
                print("  ℹ Hint: No entries exist yet — run 'python extract-knowledge.py' first.")
            else:
                print(f"No knowledge entries matched: {query!r}")
                print(f"  ℹ {total} total entries in database.")
                terms = query.strip().split()
                if len(terms) > 2:
                    shorter = " ".join(terms[:2])
                    print(f"  ℹ Try shorter query, e.g.: {shorter!r}")
                cats = db.execute("SELECT DISTINCT category FROM knowledge_entries ORDER BY category").fetchall()
                if cats:
                    cat_list = ", ".join(r[0] for r in cats)
                    print(f"  ℹ Available categories: {cat_list}")
        except sqlite3.OperationalError:
            print(f"No knowledge entries matched: {query!r}")

    selected_entry_ids = _safe_int_list(r["id"] for r in rows) if rows else []
    hit_count = len(rows)
    db.close()
    return {"hit_count": hit_count, "selected_entry_ids": selected_entry_ids}


def _export_json(rows: list):
    """Export results as JSON to stdout.

    Deserializes JSON-encoded column fields (affected_files, facts) so consumers
    receive proper arrays rather than raw JSON strings.
    """
    _JSON_ARRAY_FIELDS = ("affected_files", "facts")
    db = get_db()
    clean = []
    try:
        for r in rows:
            d = dict(r)
            d.pop("excerpt", None)
            for field in _JSON_ARRAY_FIELDS:
                if field in d and isinstance(d[field], str):
                    try:
                        d[field] = json.loads(d[field])
                    except (ValueError, TypeError):
                        pass
            d["snippet_freshness"] = _compute_snippet_freshness(d)
            entry_id = d.get("id")
            if isinstance(entry_id, int):
                d["related_entry_ids"] = _related_entry_ids(db, entry_id)
            clean.append(d)
        output = json.dumps(clean, indent=2, ensure_ascii=False, default=str)
        # Handle Windows console encoding issues
        try:
            print(output)
        except UnicodeEncodeError:
            sys.stdout.buffer.write(output.encode("utf-8"))
            sys.stdout.buffer.write(b"\n")
    finally:
        db.close()


def _source_document_from_row(row: dict) -> dict:
    return {
        "id": row.get("document_id"),
        "doc_type": row.get("doc_type"),
        "title": row.get("doc_title"),
        "file_path": row.get("doc_file_path"),
        "seq": row.get("doc_seq"),
        "section": row.get("source_section"),
    }


def _source_label_from_row(row: dict) -> str:
    doc_type = (row.get("doc_type") or "").strip()
    if not doc_type:
        return ""
    section = (row.get("source_section") or "").strip()
    seq = row.get("doc_seq")
    doc_file_path = (row.get("doc_file_path") or "").strip()
    doc_title = (row.get("doc_title") or "").strip()
    if doc_type == "checkpoint" and seq:
        base = f"from checkpoint #{seq}"
        return f"{base} / {section}" if section else base
    if doc_file_path:
        from pathlib import Path as _Path

        base = f"from {doc_type} / {_Path(doc_file_path).name}"
    elif doc_title:
        base = f"from {doc_type} / {doc_title[:60]}"
    else:
        base = f"from {doc_type}"
    return f"{base} / {section}" if section else base


def _code_location_label_from_row(row: dict) -> str:
    source_file = (row.get("source_file") or "").strip()
    if not source_file:
        return ""
    start_line = row.get("start_line")
    end_line = row.get("end_line")
    if start_line and end_line and start_line != end_line:
        return f"{source_file}:{start_line}-{end_line}"
    if start_line:
        return f"{source_file}:{start_line}"
    return source_file


def _compute_snippet_freshness(row: dict) -> str:
    """Read-time snippet freshness state: fresh|drifted|missing|unknown."""
    source_file = (row.get("source_file") or "").strip()
    code_snippet = row.get("code_snippet")

    if not source_file or code_snippet is None:
        return "unknown"

    start_line = row.get("start_line")
    end_line = row.get("end_line")
    if (
        not isinstance(start_line, int)
        or not isinstance(end_line, int)
        or start_line <= 0
        or end_line <= 0
        or end_line < start_line
    ):
        return "unknown"

    path = Path(source_file)
    if not path.exists() or not path.is_file():
        return "missing"

    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "unknown"

    content = content.replace("\r\n", "\n").replace("\r", "\n")
    lines = content.split("\n")
    if end_line > len(lines):
        return "unknown"

    current = "\n".join(lines[start_line - 1 : end_line])
    if not current:
        return "unknown"
    current_cmp = current.rstrip()
    stored_cmp = str(code_snippet).replace("\r\n", "\n").replace("\r", "\n").rstrip()
    if not stored_cmp:
        return "unknown"

    if stored_cmp.endswith("…"):
        prefix = stored_cmp[:-1]
        if not prefix:
            return "unknown"
        return "fresh" if current_cmp.startswith(prefix) else "drifted"
    return "fresh" if stored_cmp == current_cmp else "drifted"


def _related_entry_ids(db: sqlite3.Connection, entry_id: int) -> list[int]:
    """Bidirectional related IDs ordered by confidence desc, ID asc."""
    try:
        rows = db.execute(
            """
            SELECT target_id AS related_id, COALESCE(confidence, 0.0) AS rel_conf, 1 AS outgoing
            FROM knowledge_relations
            WHERE source_id = ?
            UNION ALL
            SELECT source_id AS related_id, COALESCE(confidence, 0.0) AS rel_conf, 0 AS outgoing
            FROM knowledge_relations
            WHERE target_id = ?
            """,
            (entry_id, entry_id),
        ).fetchall()
    except sqlite3.OperationalError:
        return []

    best: dict[int, tuple[float, int]] = {}
    for r in rows:
        rid = int(r["related_id"])
        conf = float(r["rel_conf"] if r["rel_conf"] is not None else 0.0)
        outgoing = int(r["outgoing"])
        prev = best.get(rid)
        if prev is None or conf > prev[0] or (conf == prev[0] and outgoing > prev[1]):
            best[rid] = (conf, outgoing)

    ranked = sorted(best.items(), key=lambda item: (-item[1][0], item[0]))
    return [int(rid) for rid, _ in ranked[:3]]


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


def semantic_search(query: str, limit: int = 10, verbose: bool = False, retrieval_query: str = None, rrf_k: int = None):
    """Hybrid search: FTS5 keyword + vector semantic, merged with RRF."""
    try:
        tools_dir = Path(__file__).parent
        sys.path.insert(0, str(tools_dir))
        from embed import ensure_embedding_tables, hybrid_search, load_config
    except ImportError:
        print("Error: embed.py not found. Falling back to keyword search.")
        return search(query, limit=limit, verbose=verbose, retrieval_query=retrieval_query)

    config = load_config()
    if rrf_k is not None:
        config["rrf_k"] = int(rrf_k)
    db = get_db()
    ensure_embedding_tables(db)

    # Issue #369: use rewritten/retrieval query for the actual search so semantic
    # and FTS paths both benefit from query condensation.
    effective_query = retrieval_query if retrieval_query else query
    results = hybrid_search(db, effective_query, config, limit=limit)

    if not results:
        print(f"No results for: {query}")
        print("Tip: Try keyword search (without --semantic) or broader terms.")
        db.close()
        return {"hit_count": 0, "selected_entry_ids": []}

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
        feedback_bias = float(r.get("feedback_bias", 0.0) or 0.0)
        feedback_count = int(r.get("feedback_count", 0) or 0)

        type_color = {
            "checkpoint": CYAN,
            "research": GREEN,
            "artifact": YELLOW,
            "plan": MAGENTA,
            "mistake": YELLOW,
            "pattern": GREEN,
            "decision": CYAN,
            "tool": MAGENTA,
        }.get(doc_type, "")

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
        feedback_fragment = ""
        if verbose and feedback_bias != 0.0:
            feedback_fragment = f" | Feedback: bias={feedback_bias:+.2f} (count={feedback_count})"
        print(
            f"   {DIM}Session:{RESET} {sid}..  "
            f"{type_color}{doc_type}{RESET}  "
            f"{DIM}Match:{RESET} {source_label}  "
            f"{DIM}RRF:{RESET} {rrf:.4f}"
            f"{feedback_fragment}"
        )

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
            wrapped = textwrap.fill(excerpt, width=90, initial_indent="   ", subsequent_indent="   ")
            print(wrapped)

        if verbose and "section_name" in r:
            print(f"   {DIM}Section: {r['section_name']}{RESET}")

        print()

    db.close()
    selected_entry_ids = _safe_int_list(r.get("id") for r in results if isinstance(r, dict))
    return {"hit_count": len(results), "selected_entry_ids": selected_entry_ids}


def query_entity_relations(entity: str):
    """Query entity_relations for a specific entity (subject or object)."""
    db = get_db()
    try:
        rows = db.execute(
            """
            SELECT subject, predicate, object, noted_at
            FROM entity_relations
            WHERE subject = ? OR object = ?
            ORDER BY noted_at DESC
        """,
            (entity, entity),
        ).fetchall()
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
        noted = f" ({r['noted_at'][:10]})" if r["noted_at"] else ""
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
        bar = "█" * min(r["cnt"] // 5, 30)
        print(f"  {r['wing']:<20} {r['cnt']:>4}  {DIM}{bar}{RESET}")
    total = sum(r["cnt"] for r in rows)
    print(f"\n  {'Total':<20} {total:>4}")
    db.close()


def list_rooms(wing: str = ""):
    """Show rooms, optionally filtered by wing."""
    db = get_db()
    if wing:
        rows = db.execute(
            """
            SELECT room, COUNT(*) as cnt
            FROM knowledge_entries
            WHERE room != '' AND wing = ?
            GROUP BY room
            ORDER BY cnt DESC
        """,
            (wing,),
        ).fetchall()
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
        wing_label = f" [{r['wing']}]" if not wing and "wing" in r.keys() else ""
        bar = "█" * min(r["cnt"] // 3, 30)
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

        subjects = db.execute("SELECT COUNT(DISTINCT subject) FROM entity_relations").fetchone()[0]
        objects = db.execute("SELECT COUNT(DISTINCT object) FROM entity_relations").fetchone()[0]
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


def show_by_file(file_path: str, limit: int = 20, verbose: bool = False, export_fmt: str = None, compact: bool = False):
    """Show knowledge entries that recorded the given file as affected."""
    db = get_db()
    # Use JSON-quoted exact match: files are stored as JSON arrays, so matching
    # '"file_path"' (with double-quotes) ensures we match the full path as an array
    # element rather than a substring of a longer path.  E.g. searching for
    # src/auth.py won't accidentally match tests/src/auth.py or src/auth.py.bak.
    quoted_pattern = f'%"{file_path}"%'
    try:
        rows = db.execute(
            """
            SELECT id, category, title, content, confidence, affected_files, task_id,
                   est_tokens
            FROM knowledge_entries
            WHERE affected_files LIKE ? AND affected_files != '[]'
            ORDER BY confidence DESC, occurrence_count DESC
            LIMIT ?
        """,
            (quoted_pattern, limit),
        ).fetchall()
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
            "mistake": YELLOW,
            "pattern": GREEN,
            "decision": CYAN,
            "tool": MAGENTA,
            "feature": GREEN,
            "refactor": DIM,
            "discovery": CYAN,
        }.get(r["category"], "")
        task_note = f"  {DIM}task={r['task_id']}{RESET}" if r["task_id"] else ""
        tok = f" {DIM}~{r['est_tokens']}tok{RESET}" if r["est_tokens"] else ""
        if compact:
            print(f"  {DIM}#{r['id']:>4d}{RESET} [{r['category']}] {r['title'][:70]}{tok}")
        else:
            print(
                f"  {DIM}#{r['id']:>4d}{RESET} {cat_color}[{r['category']}]{RESET} "
                f"{BOLD}{r['title'][:65]}{RESET}{task_note}{tok}"
            )
            if verbose and r["content"]:
                first = r["content"].split("\n")[0][:100]
                print(f"         {DIM}{first}{RESET}")
    if not compact and not verbose:
        print(f"\n{DIM}Use --verbose for content preview, --detail <id> for full entry{RESET}")
    db.close()


def show_by_module(module: str, limit: int = 20, verbose: bool = False, export_fmt: str = None, compact: bool = False):
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
    pat_mid = f"%/{module}/%"
    module_lower = module.lower()
    _fallback_used = False
    try:
        rows = db.execute(
            """
            SELECT id, category, title, content, confidence, affected_files, task_id,
                   est_tokens
            FROM knowledge_entries
            WHERE affected_files != '[]'
              AND (affected_files LIKE ? OR affected_files LIKE ?)
            ORDER BY confidence DESC, occurrence_count DESC
            LIMIT ?
        """,
            (pat_first, pat_mid, limit),
        ).fetchall()

        # Fallback: content/title search when no file-tagged entries exist.
        if not rows:
            rows = db.execute(
                """
                SELECT id, category, title, content, confidence, affected_files, task_id,
                       est_tokens
                FROM knowledge_entries
                WHERE LOWER(content) LIKE ? OR LOWER(title) LIKE ?
                ORDER BY confidence DESC, occurrence_count DESC
                LIMIT ?
            """,
                (f"%{module_lower}%", f"%{module_lower}%", limit),
            ).fetchall()
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
            "mistake": YELLOW,
            "pattern": GREEN,
            "decision": CYAN,
            "tool": MAGENTA,
            "feature": GREEN,
            "refactor": DIM,
            "discovery": CYAN,
        }.get(r["category"], "")
        task_note = f"  {DIM}task={r['task_id']}{RESET}" if r["task_id"] else ""
        tok = f" {DIM}~{r['est_tokens']}tok{RESET}" if r["est_tokens"] else ""
        if compact:
            print(f"  {DIM}#{r['id']:>4d}{RESET} [{r['category']}] {r['title'][:70]}{tok}")
        else:
            print(
                f"  {DIM}#{r['id']:>4d}{RESET} {cat_color}[{r['category']}]{RESET} "
                f"{BOLD}{r['title'][:65]}{RESET}{task_note}{tok}"
            )
            if verbose and r["content"]:
                first = r["content"].split("\n")[0][:100]
                print(f"         {DIM}{first}{RESET}")
    if not compact and not verbose:
        print(f"\n{DIM}Use --verbose for content preview, --detail <id> for full entry{RESET}")
    db.close()


def show_by_task(task_id: str, limit: int = 30, verbose: bool = False, export_fmt: str = None, compact: bool = False):
    """Show knowledge entries recorded under a specific task ID."""
    db = get_db()
    safe = task_id.strip()[:200]
    try:
        rows = db.execute(
            """
            SELECT id, category, title, content, confidence, affected_files, task_id,
                   occurrence_count, last_seen, est_tokens,
                   document_id, source_section, source_file, start_line, end_line,
                   code_language, code_snippet,
                   doc_type, doc_title, doc_file_path, doc_seq
            FROM (
                SELECT ke.id, ke.category, ke.title, ke.content, ke.confidence, ke.affected_files, ke.task_id,
                       ke.occurrence_count, ke.last_seen, ke.est_tokens,
                       ke.document_id, ke.source_section, ke.source_file, ke.start_line, ke.end_line,
                       ke.code_language, ke.code_snippet,
                       d.doc_type as doc_type, d.title as doc_title, d.file_path as doc_file_path, d.seq as doc_seq
                FROM knowledge_entries ke
                LEFT JOIN documents d ON ke.document_id = d.id
                WHERE ke.task_id = ?
                ORDER BY ke.confidence DESC, ke.occurrence_count DESC
                LIMIT ?
            )
        """,
            (safe, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        try:
            rows = db.execute(
                """
                SELECT id, category, title, content, confidence, affected_files, task_id,
                       occurrence_count, last_seen, est_tokens
                FROM knowledge_entries
                WHERE task_id = ?
                ORDER BY confidence DESC, occurrence_count DESC
                LIMIT ?
            """,
                (safe, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            if export_fmt == "json":
                import json as _json

                print(_json.dumps({"task_id": task_id, "entries": []}, indent=2))
            else:
                print("⚠ task_id column not found. Run build-session-index.py to migrate.")
            db.close()
            return {"hit_count": 0, "selected_entry_ids": [], "task_id": safe}

    if not rows:
        # Fallback: FTS search on the task_id as a query string
        if export_fmt == "json":
            import json as _json

            print(_json.dumps({"task_id": task_id, "entries": []}, indent=2))
        else:
            print(f"No entries directly tagged task_id='{task_id}'.")
            print(f"Try: python query-session.py '{task_id}' for FTS search.")
        db.close()
        return {"hit_count": 0, "selected_entry_ids": [], "task_id": safe}

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
            d["source_document"] = _source_document_from_row(d)
            d["snippet_freshness"] = _compute_snippet_freshness(d)
            entry_id = d.get("id")
            if isinstance(entry_id, int):
                d["related_entry_ids"] = _related_entry_ids(db, entry_id)
            entries.append(d)
        output = _json.dumps(
            {"task_id": task_id, "entries": entries},
            indent=2,
            ensure_ascii=False,
            default=str,
        )
        try:
            print(output)
        except UnicodeEncodeError:
            sys.stdout.buffer.write(output.encode("utf-8"))
            sys.stdout.buffer.write(b"\n")
        db.close()
        selected_entry_ids = _safe_int_list(d.get("id") for d in entries)
        return {"hit_count": len(selected_entry_ids), "selected_entry_ids": selected_entry_ids, "task_id": safe}

    print(f"\n{BOLD}Task recall: {task_id} ({len(rows)} entries){RESET}\n")
    for r in rows:
        cat_color = {
            "mistake": YELLOW,
            "pattern": GREEN,
            "decision": CYAN,
            "tool": MAGENTA,
            "feature": GREEN,
            "refactor": DIM,
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
            print(
                f"  {DIM}#{r['id']:>4d}{RESET} {cat_color}[{r['category']}]{RESET} "
                f"{BOLD}{r['title'][:65]}{RESET}{files}{tok}"
            )
            source_label = _source_label_from_row(dict(r))
            if source_label:
                print(f"         {DIM}{source_label}{RESET}")
            location_label = _code_location_label_from_row(dict(r))
            if location_label:
                print(f"         {DIM}at {location_label}{RESET}")
            if verbose and r["content"]:
                first = r["content"].split("\n")[0][:100]
                print(f"         {DIM}{first}{RESET}")
    if not compact and not verbose:
        print(f"\n{DIM}Use --verbose for content preview, --detail <id> for full entry{RESET}")
    db.close()
    return {"hit_count": len(rows), "selected_entry_ids": _safe_int_list(r["id"] for r in rows), "task_id": safe}


def show_diff_context(limit: int = 20, verbose: bool = False, export_fmt: str = None, compact: bool = False):
    """Surface knowledge entries relevant to the current git diff.

    Reads changed files from `git diff HEAD --name-only`, then queries
    knowledge entries that recorded those files (via affected_files) or
    mention them in content/title.
    """
    import subprocess

    try:
        result = subprocess.run(["git", "diff", "HEAD", "--name-only"], capture_output=True, text=True, timeout=10)
        changed = [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]
    except Exception as e:
        print(f"⚠ git diff failed: {e}")
        return

    # Also include staged files
    try:
        result2 = subprocess.run(["git", "diff", "--cached", "--name-only"], capture_output=True, text=True, timeout=10)
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
            rows = db.execute(
                """
                SELECT id, category, title, content, confidence,
                       affected_files, task_id, occurrence_count, est_tokens
                FROM knowledge_entries
                WHERE (affected_files LIKE ? AND affected_files != '[]')
                   OR LOWER(content) LIKE ?
                   OR LOWER(title) LIKE ?
                ORDER BY confidence DESC
                LIMIT ?
            """,
                (quoted_pattern, f"%{basename_lower}%", f"%{basename_lower}%", limit),
            ).fetchall()
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
            d["snippet_freshness"] = _compute_snippet_freshness(d)
            entry_id = d.get("id")
            if isinstance(entry_id, int):
                d["related_entry_ids"] = _related_entry_ids(db, entry_id)
            entries.append(d)
        output = _json.dumps({"changed_files": changed, "entries": entries}, indent=2, ensure_ascii=False, default=str)
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
                "mistake": YELLOW,
                "pattern": GREEN,
                "decision": CYAN,
                "tool": MAGENTA,
                "feature": GREEN,
                "refactor": DIM,
                "discovery": CYAN,
            }.get(r["category"], "")
            tok = f" {DIM}~{r['est_tokens']}tok{RESET}" if r["est_tokens"] else ""
            if compact:
                print(f"    {DIM}#{r['id']:>4d}{RESET} [{r['category']}] {r['title'][:60]}{tok}")
            else:
                print(f"    {DIM}#{r['id']:>4d}{RESET} {cat_color}[{r['category']}]{RESET} {r['title'][:60]}{tok}")
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


# ---------------------------------------------------------------------------
# Feedback write API (issue #707)
# ---------------------------------------------------------------------------

_VERDICT_MAP = {"good": 1, "bad": -1, "neutral": 0}


def write_feedback(entry_id: int, verdict_str: str, query: str = "") -> None:
    """Insert a feedback row for a knowledge entry into search_feedback.

    verdict_str: "good" (+1), "bad" (-1), or "neutral" (0).
    query: optional query string that surfaced this entry (may be empty).
    """
    verdict = _VERDICT_MAP.get(verdict_str)
    if verdict is None:
        print(f"Error: verdict must be one of: good, bad, neutral (got {verdict_str!r})")
        sys.exit(1)

    db = get_db()
    try:
        # Ensure table exists (graceful on older DBs)
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='search_feedback'"
        ).fetchone()
        if not exists:
            print("Error: search_feedback table not found — run 'sk index migrate' to upgrade the DB")
            sys.exit(1)

        created_at = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        db.execute(
            """
            INSERT INTO search_feedback (query, result_id, result_kind, verdict, created_at)
            VALUES (?, ?, 'knowledge', ?, ?)
            """,
            (query, str(entry_id), verdict, created_at),
        )
        db.commit()
        label = {1: "good (+1)", -1: "bad (-1)", 0: "neutral (0)"}[verdict]
        print(f"Feedback recorded: entry {entry_id} → {label}")
    except sqlite3.OperationalError as exc:
        print(f"Error writing feedback: {exc}")
        sys.exit(1)
    finally:
        db.close()


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
            args = args[:idx] + args[idx + 2 :]  # always strip --budget + its value
        else:
            budget = 3000
            args = args[:idx] + args[idx + 1 :]

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

    # Positional subcommands: label <prefix> [value]  and  labels
    if args and args[0] == "labels":
        list_session_labels()
        return

    if args and args[0] == "label":
        rest_label = [a for a in args[1:] if not a.startswith("--")]
        if not rest_label:
            print("Usage: query-session.py label <id-prefix> [label-text]")
            print("  Omit label-text to show the current label.")
            return
        prefix = rest_label[0]
        if len(rest_label) >= 2:
            set_session_label(prefix, " ".join(rest_label[1:]))
        else:
            get_session_label(prefix)
        return

    if args and args[0] == "digest":
        rest_digest = [a for a in args[1:] if not a.startswith("--")]
        if not rest_digest:
            print("Usage: query-session.py digest <id-prefix> [--json]")
            return
        sys.exit(cmd_digest(rest_digest[0], as_json="--json" in args))
        return

    if args and args[0] == "stats":
        sys.exit(cmd_stats(args[1:]))
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
            entry_id = int(args[idx + 1])
            output, meta = _run_with_capture(show_detail, entry_id)
            meta = _coerce_recall_meta(
                meta,
                {"opened_entry_id": entry_id, "hit_count": 0, "selected_entry_ids": []},
            )
            _record_recall_event(
                event_kind="detail_open",
                surface="detail",
                raw_query="",
                rewritten_query="",
                task_id="",
                selected_entry_ids=meta.get("selected_entry_ids", []),
                hit_count=meta.get("hit_count", 0),
                output_chars=len(output),
                opened_entry_id=meta.get("opened_entry_id", entry_id),
            )
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

    if "--why" in args:
        idx = args.index("--why")
        if idx + 1 < len(args):
            explain_why(int(args[idx + 1]), as_json="--json" in args)
        else:
            print("Error: --why requires an entry ID")
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

    if "--feedback" in args:
        idx = args.index("--feedback")
        if idx + 2 < len(args):
            try:
                fb_entry_id = int(args[idx + 1])
            except ValueError:
                print("Error: --feedback requires a numeric entry ID")
                return
            fb_verdict = args[idx + 2]
            # Optional query context: any non-flag trailing args
            fb_query = " ".join(a for a in args if not a.startswith("--") and a not in (args[idx + 1], fb_verdict))
            write_feedback(fb_entry_id, fb_verdict, query=fb_query)
        else:
            print("Error: --feedback requires <entry_id> <good|bad|neutral>")
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
            show_by_file(args[idx + 1], limit=limit, verbose=verbose, export_fmt=export_fmt, compact=compact)
        else:
            print("Error: --file requires a file path")
        return

    if "--module" in args:
        idx = args.index("--module")
        if idx + 1 < len(args):
            show_by_module(args[idx + 1], limit=limit, verbose=verbose, export_fmt=export_fmt, compact=compact)
        else:
            print("Error: --module requires a module/directory name")
        return

    if "--task" in args:
        idx = args.index("--task")
        if idx + 1 < len(args):
            task_id = args[idx + 1]
            if export_fmt == "json":
                output, meta = _run_with_capture(
                    show_by_task, task_id, limit=limit, verbose=verbose, export_fmt=export_fmt, compact=compact
                )
                meta = meta or {"hit_count": 0, "selected_entry_ids": [], "task_id": task_id}
                _record_recall_event(
                    event_kind="recall",
                    surface="task_json",
                    raw_query=task_id,
                    rewritten_query=(meta.get("task_id") or task_id),
                    task_id=(meta.get("task_id") or task_id),
                    selected_entry_ids=meta.get("selected_entry_ids", []),
                    hit_count=meta.get("hit_count", 0),
                    output_chars=len(output),
                )
            else:
                show_by_task(task_id, limit=limit, verbose=verbose, export_fmt=export_fmt, compact=compact)
        else:
            print("Error: --task requires a task ID")
        return

    if "--diff" in args:
        show_diff_context(limit=limit, verbose=verbose, export_fmt=export_fmt, compact=compact)
        return

    # ── Batch C: new session-focused flags ─────────────────────────────────

    # --session-raw: dump raw document content for a session (requires --from)
    if "--session-raw" in args:
        from_id = None
        if "--from" in args:
            fi = args.index("--from")
            from_id = args[fi + 1] if fi + 1 < len(args) and not args[fi + 1].startswith("--") else None
        if from_id:
            show_session_raw(from_id)
        else:
            print("Error: --session-raw requires --from <session-id>")
        return

    # Parse Batch C search modifiers (used in default search below)
    column_filter_str = None
    if "--in" in args:
        idx = args.index("--in")
        column_filter_str = args[idx + 1] if idx + 1 < len(args) and not args[idx + 1].startswith("--") else None

    session_filter = None
    if "--from" in args:
        idx = args.index("--from")
        session_filter = args[idx + 1] if idx + 1 < len(args) and not args[idx + 1].startswith("--") else None

    show_snippet = "--no-snippet" not in args  # default: on

    # Resolve --in to FTS5 column name and snippet column index
    col_name = None
    snip_col = 2  # default: user_messages (col index 2, empirically verified)
    if column_filter_str:
        if column_filter_str in _SESSION_COL_MAP:
            col_name, snip_col = _SESSION_COL_MAP[column_filter_str]
        else:
            valid = ", ".join(_SESSION_COL_MAP.keys())
            print(f"{DIM}Warning: --in {column_filter_str!r} unknown; valid: {valid}{RESET}")

    # Knowledge category shortcuts (export_fmt already parsed above)

    # Error-type filter for knowledge entries
    error_type_filter = None
    if "--error-type" in args:
        idx = args.index("--error-type")
        error_type_filter = args[idx + 1] if idx + 1 < len(args) and not args[idx + 1].startswith("--") else None

    # --since YYYY-MM-DD / --days N: restrict knowledge entries to those seen on/after date
    import datetime as _dt_qs
    since_date_filter: "str | None" = None
    if "--since" in args:
        idx = args.index("--since")
        if idx + 1 < len(args) and not args[idx + 1].startswith("--"):
            since_date_filter = args[idx + 1]
    if "--days" in args and since_date_filter is None:
        idx = args.index("--days")
        try:
            n_days = int(args[idx + 1]) if idx + 1 < len(args) and not args[idx + 1].startswith("--") else 7
            since_date_filter = (
                _dt_qs.datetime.now(_dt_qs.timezone.utc) - _dt_qs.timedelta(days=n_days)
            ).strftime("%Y-%m-%d")
        except (ValueError, IndexError):
            pass

    # limit/verbose already parsed above; re-read for semantic/search paths
    for shortcut, category in [
        ("--mistakes", "mistake"),
        ("--patterns", "pattern"),
        ("--decisions", "decision"),
        ("--tools", "tool"),
    ]:
        if shortcut in args:
            show_knowledge(category, limit, export_fmt, source_filter, verbose, compact=compact)
            return

    # Check for semantic mode
    use_semantic = "--semantic" in args or "-s" in args
    # Issue #371: synonym expansion flag
    use_expand_synonyms = "--expand-synonyms" in args
    # Issue #375: configurable RRF k
    rrf_k_override = None
    if "--rrf-k" in args:
        try:
            _rrf_idx = args.index("--rrf-k")
            rrf_k_override = int(args[_rrf_idx + 1])
        except (ValueError, IndexError):
            pass

    # Default: search mode
    query_parts = []
    doc_type = None
    verbose = False

    i = 0
    while i < len(args):
        if args[i] == "--type" and i + 1 < len(args):
            doc_type = args[i + 1]
            i += 2
        elif args[i] in ("--limit", "--export", "--source", "--in", "--from", "--error-type", "--since", "--days"):
            i += 2  # skip flag + value (already parsed)
        elif args[i] in ("--verbose", "-v"):
            verbose = True
            i += 1
        elif args[i] in ("--semantic", "-s"):
            i += 1
        elif args[i] in ("--expand-synonyms",):
            i += 1  # already captured above
        elif args[i] in ("--rrf-k",):
            i += 2  # skip flag + value (already parsed above)
        elif args[i] in ("--compact", "--snippet", "--no-snippet"):
            i += 1  # already consumed or toggle flags
        elif args[i] in ("--agent-tag", "--msg-tag"):
            i += 2  # skip flag + value; tag filters must not enter query text
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
    rewritten_query = _rewrite_query_local(query)
    # Issue #371: optionally expand synonyms. For FTS paths we use the OR-query
    # form (_expand_synonyms_fts) so that any synonym triggers a hit; for
    # semantic paths we use the plain expanded string (_expand_synonyms) so the
    # embedding model sees all synonym terms in the query text without
    # FTS-specific syntax (quotes, asterisks, OR operators).
    semantic_query = rewritten_query  # plain text; used by embedding/semantic path
    if use_expand_synonyms:
        semantic_query = _expand_synonyms(rewritten_query)
        rewritten_query = _expand_synonyms_fts(rewritten_query)

    if use_semantic:
        output, meta = _run_with_capture(semantic_search, query, limit, verbose, semantic_query, rrf_k_override)
        meta = meta or {"hit_count": 0, "selected_entry_ids": []}
        _record_recall_event(
            event_kind="recall",
            surface="semantic",
            raw_query=query,
            rewritten_query=semantic_query,
            task_id="",
            selected_entry_ids=meta.get("selected_entry_ids", []),
            hit_count=meta.get("hit_count", 0),
            output_chars=len(output),
        )
    elif export_fmt:
        output, meta = _run_with_capture(
            search, query, doc_type, limit, verbose, source_filter, session_filter, rewritten_query
        )
        meta = _coerce_recall_meta(meta, {"hit_count": 0, "selected_entry_ids": []})
        _record_recall_event(
            event_kind="recall",
            surface="search",
            raw_query=query,
            rewritten_query=rewritten_query,
            task_id="",
            selected_entry_ids=meta.get("selected_entry_ids", []),
            hit_count=meta.get("hit_count", 0),
            output_chars=len(output),
        )
    else:

        def _run_default_search_surface():
            search_meta = _coerce_recall_meta(
                search(
                    query,
                    doc_type,
                    limit,
                    verbose,
                    source_filter,
                    session_filter,
                    rewritten_query,
                ),
                {"hit_count": 0, "selected_entry_ids": []},
            )
            total_hit_count = int(search_meta.get("hit_count", 0) or 0)
            selected_entry_ids = list(search_meta.get("selected_entry_ids", []))

            # Batch C: also search sessions_fts (BM25 + column-scoped)
            session_hits = search_sessions_fts(
                query,
                session_id_filter=session_filter,
                col_name=col_name,
                snippet_col=snip_col,
                show_snippet=show_snippet,
                limit=limit,
                retrieval_query=rewritten_query,
            )
            total_hit_count += len(session_hits)
            if session_hits:
                print(f"\n{BOLD}Session hits ({len(session_hits)} results){RESET}\n")
                for i, r in enumerate(session_hits, 1):
                    sid = r["session_id"][:8]
                    excerpt = (r["excerpt"] or "").replace("<mark>", f"{BOLD}{YELLOW}").replace("</mark>", RESET)
                    print(f"  {BOLD}{i}.{RESET} {r['title'][:55]} {DIM}[session: {sid}...]{RESET}")
                    if show_snippet and excerpt.strip():
                        short = excerpt[:120].replace("\n", " ").strip()
                        print(f"     {DIM}{short}{RESET}")

            # Also search knowledge entries
            knowledge_meta = _coerce_recall_meta(
                search_knowledge(query, limit=5, retrieval_query=rewritten_query, since_date=since_date_filter),
                {"hit_count": 0, "selected_entry_ids": []},
            )
            total_hit_count += int(knowledge_meta.get("hit_count", 0) or 0)
            selected_entry_ids.extend(knowledge_meta.get("selected_entry_ids", []))

            return {
                "hit_count": total_hit_count,
                "selected_entry_ids": _safe_int_list(selected_entry_ids),
            }

        output, meta = _run_with_capture(_run_default_search_surface)
        meta = _coerce_recall_meta(meta, {"hit_count": 0, "selected_entry_ids": []})
        _record_recall_event(
            event_kind="recall",
            surface="search",
            raw_query=query,
            rewritten_query=rewritten_query,
            task_id="",
            selected_entry_ids=meta.get("selected_entry_ids", []),
            hit_count=meta.get("hit_count", 0),
            output_chars=len(output),
        )


if __name__ == "__main__":
    main()
