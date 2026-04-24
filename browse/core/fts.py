"""browse/core/fts.py — FTS helpers, DB helpers, HTML escape. Verbatim from browse.py."""
import html
import os
import re
import sqlite3
import sys
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

_DEFAULT_DB = Path.home() / ".copilot" / "session-state" / "knowledge.db"
_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9._-]{1,128}$")

# Column name → (fts5_col_name, snippet_col_index)
# sessions_fts layout: 0=session_id(UNINDEXED), 1=title, 2=user_messages,
#                      3=assistant_messages, 4=tool_names
_SESSION_COL_MAP: dict = {
    "user":      ("user_messages",      2),
    "assistant": ("assistant_messages", 3),
    "tools":     ("tool_names",         4),
    "title":     ("title",              1),
}


def _sanitize_fts_query(query: str, max_length: int = 500) -> str:
    """Sanitize user input for FTS5 MATCH queries. Source: query-session.py."""
    query = query.strip()[:max_length]
    fts_special = set('"*(){}:^')
    cleaned = "".join(c if c not in fts_special else " " for c in query)
    terms = []
    for t in cleaned.split():
        if t.upper() not in ("OR", "AND", "NOT", "NEAR"):
            terms.append(t)
    if not terms:
        return '""'
    return " ".join(f'"{t}"*' for t in terms)


def _build_column_scoped_query(sanitized_term: str, columns: list) -> str:
    """Build column-scoped FTS5 query. Source: query-session.py."""
    if not columns:
        return sanitized_term
    col_filter = " ".join(columns)
    return f"{{{col_filter}}}: {sanitized_term}"


def _esc(s: object) -> str:
    """HTML-escape a value. MUST be called on ALL dynamic content before insertion."""
    return html.escape(str(s) if s is not None else "", quote=True)


def _open_db(db_path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(db_path), check_same_thread=False)
    db.row_factory = sqlite3.Row
    return db


def _probe_sessions_fts(db: sqlite3.Connection) -> bool:
    """Return True if sessions_fts table exists with columns."""
    try:
        rows = list(db.execute("PRAGMA table_info(sessions_fts)"))
        return len(rows) > 0
    except Exception:
        return False


def _get_schema_version(db: sqlite3.Connection) -> int:
    try:
        row = db.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except Exception:
        return 0


def _count_sessions(db: sqlite3.Connection) -> int:
    try:
        row = db.execute("SELECT COUNT(*) FROM sessions").fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0
