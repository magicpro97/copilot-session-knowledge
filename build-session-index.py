#!/usr/bin/env python3
"""
build-session-index.py — Index all Copilot/Claude session-state into SQLite FTS5

Parses checkpoints, research docs, files/artifacts, plan.md, and Claude Code JSONL
sessions into a searchable knowledge database.

Usage:
    python build-session-index.py                    # Full rebuild (Copilot only)
    python build-session-index.py --incremental      # Only new/changed files
    python build-session-index.py --stats            # Show index statistics
    python build-session-index.py --no-embed         # Skip embedding generation
    python build-session-index.py --claude           # Index Claude Code sessions only
    python build-session-index.py --all              # Index both Copilot + Claude
"""

import sqlite3
import re
import os
import sys
import hashlib
from pathlib import Path
from datetime import datetime

# Fix Windows console encoding for Unicode output
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SESSION_STATE = Path.home() / ".copilot" / "session-state"
DB_PATH = SESSION_STATE / "knowledge.db"

CHECKPOINT_SECTIONS = [
    "overview", "history", "work_done", "technical_details",
    "important_files", "next_steps"
]


def create_db(db_path: Path) -> sqlite3.Connection:
    """Create database schema with FTS5 support."""
    db = sqlite3.connect(str(db_path))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    # Quick integrity check on existing databases
    if db_path.exists():
        result = db.execute("PRAGMA quick_check").fetchone()
        if result[0] != "ok":
            print(f"⚠ Database integrity issue: {result[0]}", file=sys.stderr)

    db.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            summary TEXT DEFAULT '',
            total_checkpoints INTEGER DEFAULT 0,
            total_research INTEGER DEFAULT 0,
            total_files INTEGER DEFAULT 0,
            has_plan INTEGER DEFAULT 0,
            source TEXT DEFAULT 'copilot',
            indexed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            doc_type TEXT NOT NULL,        -- 'checkpoint', 'research', 'artifact', 'plan', 'claude-session'
            seq INTEGER DEFAULT 0,         -- checkpoint sequence number
            title TEXT NOT NULL,
            file_path TEXT NOT NULL UNIQUE,
            file_hash TEXT,                -- MD5 for incremental updates
            size_bytes INTEGER DEFAULT 0,
            content_preview TEXT DEFAULT '',-- first 500 chars of content
            source TEXT DEFAULT 'copilot',
            indexed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            section_name TEXT NOT NULL,     -- 'overview', 'history', etc. or 'full' for non-checkpoints
            content TEXT NOT NULL,
            UNIQUE(document_id, section_name)
        );

        -- FTS5 virtual table for full-text search
        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
            title,
            section_name,
            content,
            doc_type,
            session_id UNINDEXED,
            document_id UNINDEXED,
            tokenize='unicode61 remove_diacritics 2'
        );

        CREATE INDEX IF NOT EXISTS idx_documents_session ON documents(session_id);
        CREATE INDEX IF NOT EXISTS idx_documents_type ON documents(doc_type);
        CREATE INDEX IF NOT EXISTS idx_sections_doc ON sections(document_id);
    """)

    # Create event_offsets table for byte-offset seek (Batch B v7).
    db.executescript("""
        CREATE TABLE IF NOT EXISTS event_offsets (
            session_id TEXT NOT NULL,
            event_id INTEGER NOT NULL,
            byte_offset INTEGER NOT NULL,
            file_mtime REAL NOT NULL,
            PRIMARY KEY (session_id, event_id)
        );
        CREATE INDEX IF NOT EXISTS idx_event_offsets_session ON event_offsets(session_id);
    """)

    # Migrate existing databases: add source column if missing, then create indexes
    _migrate_add_source(db)

    return db


def _migrate_add_source(db: sqlite3.Connection):
    """Add 'source' column and Phase 6/7 columns to existing tables (safe, idempotent)."""
    migrations = [
        ("sessions", "source", "TEXT DEFAULT 'copilot'"),
        ("documents", "source", "TEXT DEFAULT 'copilot'"),
        ("knowledge_entries", "source", "TEXT DEFAULT 'copilot'"),
        # Phase 6B: topic key, dedup, revision tracking
        ("knowledge_entries", "topic_key", "TEXT"),
        ("knowledge_entries", "revision_count", "INTEGER DEFAULT 1"),
        ("knowledge_entries", "content_hash", "TEXT"),
        # Phase 7: task-scoped recall + file/module surface
        ("knowledge_entries", "task_id", "TEXT DEFAULT ''"),
        ("knowledge_entries", "affected_files", "TEXT DEFAULT '[]'"),
        # Batch B v7: two-phase indexing columns on sessions
        ("sessions", "file_mtime", "REAL"),
        ("sessions", "indexed_at_r", "REAL"),
        ("sessions", "fts_indexed_at", "REAL"),
        ("sessions", "event_count_estimate", "INTEGER DEFAULT 0"),
        ("sessions", "file_size_bytes", "INTEGER DEFAULT 0"),
    ]
    _ALLOWED_TABLES = {"sessions", "documents", "knowledge_entries"}
    for table, col, col_def in migrations:
        assert table in _ALLOWED_TABLES, f"Unexpected table: {table}"
        try:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}")
        except sqlite3.OperationalError:
            pass  # Column already exists

    # Add indexes (safe with IF NOT EXISTS)
    try:
        db.execute("CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_sessions_source ON sessions(source)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_ke_source ON knowledge_entries(source)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_ke_topic ON knowledge_entries(topic_key)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_ke_hash ON knowledge_entries(content_hash)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_ke_task ON knowledge_entries(task_id)")
    except sqlite3.OperationalError:
        pass  # Table might not exist yet

    # Phase 6C: knowledge relations table
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER REFERENCES knowledge_entries(id),
                target_id INTEGER REFERENCES knowledge_entries(id),
                relation_type TEXT NOT NULL,
                confidence REAL DEFAULT 0.8,
                created_at TEXT,
                UNIQUE(source_id, target_id, relation_type)
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_kr_source ON knowledge_relations(source_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_kr_target ON knowledge_relations(target_id)")
    except sqlite3.OperationalError:
        pass


def file_hash(path: Path) -> str:
    """Compute MD5 hash of file content."""
    return hashlib.md5(path.read_bytes()).hexdigest()


# ──────────────────────────────────────────────
# Batch B: two-phase indexing infrastructure
# ──────────────────────────────────────────────

# Noise filter: system events matching these patterns are dropped from FTS.
# Only applies when kind == "system" (B-BL-01: use kind not role).
# Scope cut: note kind is never indexed (contract §scope_cuts).
_NOISE_PATTERNS = [
    re.compile(r"^<context>", re.IGNORECASE),       # XML context blocks
    re.compile(r"^<system>", re.IGNORECASE),         # XML system blocks
    re.compile(r"you are claude", re.IGNORECASE),    # AI persona boilerplate
    re.compile(r"the assistant is claude", re.IGNORECASE),  # persona variant
    re.compile(r"^here are some instructions", re.IGNORECASE),  # instruction preamble
    re.compile(r"^this is a conversation", re.IGNORECASE),       # conversation header
]


def _is_system_boilerplate(event) -> bool:
    """Return True if event is a system-kind boilerplate that should be dropped from FTS.

    Noise filter (Batch B): drops system events whose content matches known
    low-information patterns.  kind == 'note' events are also excluded from
    FTS per contract scope cuts.
    """
    if event.kind == "note":
        return True  # notes never indexed per scope cut
    if event.kind != "system":
        return False
    content = event.content or ""
    return any(pat.search(content) for pat in _NOISE_PATTERNS)


def should_skip_session(db: sqlite3.Connection, session_id: str, file_mtime: float) -> bool:
    """Return True if the session FTS is up-to-date and can be skipped.

    Change detection: skip when sessions.file_mtime == current mtime
    AND sessions.fts_indexed_at >= file_mtime (both phases complete).
    """
    row = db.execute(
        "SELECT file_mtime, fts_indexed_at FROM sessions WHERE id = ?",
        (session_id,)
    ).fetchone()
    if row is None:
        return False
    stored_mtime, fts_indexed_at = row
    if stored_mtime is None or fts_indexed_at is None:
        return False
    return (stored_mtime == file_mtime) and (fts_indexed_at >= file_mtime)


def phase1_upsert_session(
    db: sqlite3.Connection,
    session_id: str,
    path_str: str,
    source: str,
    file_mtime: float,
    file_size_bytes: int,
    event_count_estimate: int,
) -> None:
    """Phase 1: fast metadata upsert — no content read, no FTS writes.

    Populates/updates sessions row with identity + stat columns.
    Sets indexed_at_r to now().  Does NOT touch fts_indexed_at
    (that is Phase 2's responsibility).
    """
    now = datetime.now().timestamp()
    db.execute("""
        INSERT INTO sessions (id, path, source, file_mtime, file_size_bytes,
                              event_count_estimate, indexed_at_r, indexed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            path = excluded.path,
            source = excluded.source,
            file_mtime = excluded.file_mtime,
            file_size_bytes = excluded.file_size_bytes,
            event_count_estimate = excluded.event_count_estimate,
            indexed_at_r = excluded.indexed_at_r
    """, (session_id, path_str, source, file_mtime, file_size_bytes,
          event_count_estimate, now, datetime.now().isoformat()))


def phase2_index_events(
    db: sqlite3.Connection,
    session_id: str,
    file_mtime: float,
    provider,
    session_meta,
) -> int:
    """Phase 2: content indexing via provider.iter_events_with_offset().

    Before indexing:
      - DELETE FROM knowledge_fts WHERE session_id = ?  (B-BL-06: no duplicates)
      - DELETE FROM event_offsets WHERE session_id = ?

    Iterates events, applies noise filter, inserts FTS rows and event_offsets.
    At end: sets fts_indexed_at = now() on sessions row.

    Returns number of FTS rows inserted.
    """
    # Ensure document row exists for FK (use session as a pseudo-document)
    doc_id = _get_or_create_session_document(db, session_id, session_meta)

    # B-BL-06: DELETE before re-index to prevent FTS duplication on crash recovery.
    db.execute("DELETE FROM knowledge_fts WHERE session_id = ?", (session_id,))
    db.execute("DELETE FROM event_offsets WHERE session_id = ?", (session_id,))

    inserted = 0
    for event, byte_offset in provider.iter_events_with_offset(session_meta, from_event=0):
        # Noise filter: skip system boilerplate and notes (scope cut).
        if _is_system_boilerplate(event):
            continue

        # Insert FTS row
        db.execute("""
            INSERT INTO knowledge_fts (title, section_name, content, doc_type, session_id, document_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            session_meta.title or session_id[:8],
            event.kind,
            event.content,
            "claude-session" if session_meta.provider == "claude" else "copilot-session",
            session_id,
            doc_id,
        ))

        # Insert event_offsets row (byte_offset may be -1 for non-Claude providers)
        db.execute("""
            INSERT OR REPLACE INTO event_offsets (session_id, event_id, byte_offset, file_mtime)
            VALUES (?, ?, ?, ?)
        """, (session_id, event.event_id, byte_offset, file_mtime))

        inserted += 1

    # Mark Phase 2 complete
    db.execute(
        "UPDATE sessions SET fts_indexed_at = ? WHERE id = ?",
        (datetime.now().timestamp(), session_id)
    )

    return inserted


def _get_or_create_session_document(
    db: sqlite3.Connection, session_id: str, session_meta
) -> int:
    """Return document_id for the session pseudo-document, creating it if needed."""
    path_str = str(session_meta.path)
    row = db.execute(
        "SELECT id FROM documents WHERE file_path = ?", (path_str,)
    ).fetchone()
    if row:
        return row[0]

    db.execute("""
        INSERT OR IGNORE INTO documents
            (session_id, doc_type, seq, title, file_path, size_bytes, indexed_at)
        VALUES (?, 'claude-session', 0, ?, ?, ?, ?)
    """, (
        session_id,
        session_meta.title or session_id[:8],
        path_str,
        session_meta.path.stat().st_size if session_meta.path.exists() else 0,
        datetime.now().isoformat(),
    ))
    return db.execute(
        "SELECT id FROM documents WHERE file_path = ?", (path_str,)
    ).fetchone()[0]


def extract_section(content: str, tag: str) -> str:
    """Extract XML-tagged section from checkpoint markdown."""
    match = re.search(f"<{tag}>(.*?)</{tag}>", content, re.DOTALL)
    return match.group(1).strip() if match else ""


def title_from_filename(filename: str) -> str:
    """Convert slug filename to readable title."""
    name = Path(filename).stem
    # Remove leading sequence numbers like 001-
    name = re.sub(r"^\d{3}-", "", name)
    # Replace hyphens with spaces, title case
    return name.replace("-", " ").strip().title()


def parse_checkpoint_index(session_dir: Path) -> list[dict]:
    """Parse checkpoints/index.md to get checkpoint list."""
    index_path = session_dir / "checkpoints" / "index.md"
    if not index_path.exists():
        return []

    entries = []
    for line in index_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r"\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|", line)
        if m:
            entries.append({
                "seq": int(m.group(1)),
                "title": m.group(2).strip(),
                "file": m.group(3).strip(),
            })
    return entries


def index_checkpoint(db: sqlite3.Connection, session_id: str,
                     cp_path: Path, seq: int, title: str, incremental: bool) -> bool:
    """Index a single checkpoint file. Returns True if indexed."""
    if not cp_path.exists():
        return False

    fhash = file_hash(cp_path)
    path_str = str(cp_path)

    if incremental:
        existing = db.execute(
            "SELECT file_hash FROM documents WHERE file_path = ?", (path_str,)
        ).fetchone()
        if existing and existing[0] == fhash:
            return False

    content = cp_path.read_text(encoding="utf-8", errors="ignore")
    preview = content[:500].replace("\n", " ")

    # Upsert document
    db.execute("""
        INSERT INTO documents (session_id, doc_type, seq, title, file_path, file_hash, size_bytes, content_preview, indexed_at)
        VALUES (?, 'checkpoint', ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_path) DO UPDATE SET
            file_hash=excluded.file_hash, size_bytes=excluded.size_bytes,
            content_preview=excluded.content_preview, indexed_at=excluded.indexed_at
    """, (session_id, seq, title, path_str, fhash, cp_path.stat().st_size, preview, datetime.now().isoformat()))

    doc_id = db.execute("SELECT id FROM documents WHERE file_path = ?", (path_str,)).fetchone()[0]

    # Delete old sections and FTS entries
    db.execute("DELETE FROM knowledge_fts WHERE document_id = ?", (doc_id,))
    db.execute("DELETE FROM sections WHERE document_id = ?", (doc_id,))

    # Extract and index each section
    for section_name in CHECKPOINT_SECTIONS:
        section_content = extract_section(content, section_name)
        if section_content:
            db.execute(
                "INSERT INTO sections (document_id, section_name, content) VALUES (?, ?, ?)",
                (doc_id, section_name, section_content)
            )
            db.execute("""
                INSERT INTO knowledge_fts (title, section_name, content, doc_type, session_id, document_id)
                VALUES (?, ?, ?, 'checkpoint', ?, ?)
            """, (title, section_name, section_content, session_id, doc_id))

    return True


def index_generic_doc(db: sqlite3.Connection, session_id: str,
                      doc_path: Path, doc_type: str, incremental: bool) -> bool:
    """Index a research doc, artifact, or plan.md. Returns True if indexed."""
    if not doc_path.exists():
        return False

    fhash = file_hash(doc_path)
    path_str = str(doc_path)

    if incremental:
        existing = db.execute(
            "SELECT file_hash FROM documents WHERE file_path = ?", (path_str,)
        ).fetchone()
        if existing and existing[0] == fhash:
            return False

    content = doc_path.read_text(encoding="utf-8", errors="ignore")
    title = title_from_filename(doc_path.name)
    if doc_type == "plan":
        title = "Plan"
    preview = content[:500].replace("\n", " ")

    db.execute("""
        INSERT INTO documents (session_id, doc_type, seq, title, file_path, file_hash, size_bytes, content_preview, indexed_at)
        VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_path) DO UPDATE SET
            file_hash=excluded.file_hash, size_bytes=excluded.size_bytes,
            content_preview=excluded.content_preview, indexed_at=excluded.indexed_at
    """, (session_id, doc_type, title, path_str, fhash, doc_path.stat().st_size, preview, datetime.now().isoformat()))

    doc_id = db.execute("SELECT id FROM documents WHERE file_path = ?", (path_str,)).fetchone()[0]

    # Delete old and re-index
    db.execute("DELETE FROM knowledge_fts WHERE document_id = ?", (doc_id,))
    db.execute("DELETE FROM sections WHERE document_id = ?", (doc_id,))

    # Store full content as single section
    db.execute(
        "INSERT INTO sections (document_id, section_name, content) VALUES (?, 'full', ?)",
        (doc_id, content)
    )
    db.execute("""
        INSERT INTO knowledge_fts (title, section_name, content, doc_type, session_id, document_id)
        VALUES (?, 'full', ?, ?, ?, ?)
    """, (title, content, doc_type, session_id, doc_id))

    return True


def index_session(db: sqlite3.Connection, session_dir: Path, incremental: bool) -> dict:
    """Index all content in a session directory. Returns stats."""
    session_id = session_dir.name
    stats = {"checkpoints": 0, "research": 0, "files": 0, "plan": 0}

    # Ensure session row exists first (FK constraint)
    db.execute("""
        INSERT INTO sessions (id, path, summary, indexed_at)
        VALUES (?, ?, '', ?)
        ON CONFLICT(id) DO NOTHING
    """, (session_id, str(session_dir), datetime.now().isoformat()))

    # 1. Checkpoints
    checkpoints = parse_checkpoint_index(session_dir)
    for cp in checkpoints:
        cp_path = session_dir / "checkpoints" / cp["file"]
        if index_checkpoint(db, session_id, cp_path, cp["seq"], cp["title"], incremental):
            stats["checkpoints"] += 1

    # 2. Research docs
    research_dir = session_dir / "research"
    if research_dir.exists():
        for f in research_dir.glob("*.md"):
            if index_generic_doc(db, session_id, f, "research", incremental):
                stats["research"] += 1

    # 3. Files/artifacts
    files_dir = session_dir / "files"
    if files_dir.exists():
        for f in files_dir.iterdir():
            if f.is_file() and f.suffix in (".md", ".txt"):
                if index_generic_doc(db, session_id, f, "artifact", incremental):
                    stats["files"] += 1

    # 4. plan.md
    plan_path = session_dir / "plan.md"
    if plan_path.exists() and plan_path.stat().st_size > 50:
        if index_generic_doc(db, session_id, plan_path, "plan", incremental):
            stats["plan"] = 1

    # Get summary from latest checkpoint overview
    summary = ""
    if checkpoints:
        latest = session_dir / "checkpoints" / checkpoints[-1]["file"]
        if latest.exists():
            content = latest.read_text(encoding="utf-8", errors="ignore")
            summary = extract_section(content, "overview")[:500]

    # Upsert session
    db.execute("""
        INSERT INTO sessions (id, path, summary, total_checkpoints, total_research, total_files, has_plan, indexed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            summary=excluded.summary, total_checkpoints=excluded.total_checkpoints,
            total_research=excluded.total_research, total_files=excluded.total_files,
            has_plan=excluded.has_plan, indexed_at=excluded.indexed_at
    """, (
        session_id, str(session_dir), summary,
        len(checkpoints),
        len(list(research_dir.glob("*.md"))) if research_dir.exists() else 0,
        len([f for f in files_dir.iterdir() if f.is_file() and f.suffix in (".md", ".txt")]) if files_dir.exists() else 0,
        1 if plan_path.exists() and plan_path.stat().st_size > 50 else 0,
        datetime.now().isoformat()
    ))

    return stats


def show_stats(db: sqlite3.Connection):
    """Print index statistics."""
    sessions = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    docs = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    sections = db.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
    fts_rows = db.execute("SELECT COUNT(*) FROM knowledge_fts").fetchone()[0]

    print(f"\n{'='*50}")
    print(f"  Knowledge Index Statistics")
    print(f"{'='*50}")
    print(f"  Sessions:    {sessions}")
    print(f"  Documents:   {docs}")
    print(f"  Sections:    {sections}")
    print(f"  FTS entries: {fts_rows}")
    print(f"  DB size:     {DB_PATH.stat().st_size / 1024:.1f} KB")
    print(f"{'='*50}")

    # Breakdown by type
    print("\n  By document type:")
    for row in db.execute("SELECT doc_type, COUNT(*) FROM documents GROUP BY doc_type ORDER BY doc_type"):
        print(f"    {row[0]:12s}: {row[1]}")

    # Sessions summary
    print("\n  Sessions:")
    for row in db.execute("""
        SELECT s.id, s.total_checkpoints,
               (SELECT COUNT(*) FROM documents d WHERE d.session_id = s.id) as docs,
               SUBSTR(s.summary, 1, 80)
        FROM sessions s ORDER BY s.indexed_at DESC
    """):
        sid = row[0][:8]
        print(f"    {sid}... {row[1]:2d} cp, {row[2]:2d} docs | {row[3]}...")


def _run_two_phase_claude(db: sqlite3.Connection, incremental: bool) -> None:
    """Two-phase Claude session indexing using ClaudeProvider.

    Phase 1: stat all sessions, upsert sessions row (file_mtime, size, event_count_estimate).
    Phase 2: for each session that changed, delete old FTS/offsets then re-index events.

    Change detection: skip session if file_mtime unchanged and fts_indexed_at is set.
    Noise filter applied per _is_system_boilerplate().
    """
    tools_dir = Path(__file__).parent
    sys.path.insert(0, str(tools_dir))
    try:
        from providers import ClaudeProvider
    except ImportError as exc:
        print(f"  [two-phase] Cannot import ClaudeProvider: {exc}")
        return

    provider = ClaudeProvider()
    sessions_found = 0
    phase1_count = 0
    phase2_count = 0

    for session_meta in provider.list_sessions():
        sessions_found += 1
        try:
            stat = session_meta.path.stat()
        except OSError:
            continue

        file_mtime = stat.st_mtime
        file_size = stat.st_size
        # Estimate event count from line count (JSONL)
        try:
            with open(session_meta.path, "rb") as fh:
                event_count_est = sum(1 for _ in fh)
        except OSError:
            event_count_est = 0

        # Phase 1: fast metadata upsert
        phase1_upsert_session(
            db,
            session_id=session_meta.id,
            path_str=str(session_meta.path),
            source=session_meta.provider,
            file_mtime=file_mtime,
            file_size_bytes=file_size,
            event_count_estimate=event_count_est,
        )
        phase1_count += 1

        # Change detection: skip Phase 2 if session unchanged and FTS is current
        if incremental and should_skip_session(db, session_meta.id, file_mtime):
            continue

        # Phase 2: content indexing
        try:
            inserted = phase2_index_events(
                db,
                session_id=session_meta.id,
                file_mtime=file_mtime,
                provider=provider,
                session_meta=session_meta,
            )
            if inserted > 0:
                print(f"  {session_meta.id[:8]}... Phase 2: {inserted} events indexed")
            phase2_count += 1
        except Exception as exc:
            print(f"  {session_meta.id[:8]}... Phase 2 ERROR: {exc}", file=sys.stderr)

    db.commit()

    if sessions_found == 0:
        print("  No Claude Code sessions found.")
    else:
        print(f"Claude (two-phase): {sessions_found} sessions scanned, "
              f"{phase1_count} Phase-1, {phase2_count} Phase-2 re-indexed")


def main():
    incremental = "--incremental" in sys.argv
    stats_only = "--stats" in sys.argv
    with_embeddings = "--no-embed" not in sys.argv  # Auto-embed by default
    with_claude = "--claude" in sys.argv
    all_sources = "--all" in sys.argv

    if not SESSION_STATE.exists():
        print(f"Error: Session state directory not found: {SESSION_STATE}")
        sys.exit(1)

    db = create_db(DB_PATH)

    if stats_only:
        show_stats(db)
        db.close()
        return

    mode = "incremental" if incremental else "full rebuild"

    # Index Copilot sessions (default unless --claude-only)
    if not with_claude or all_sources:
        print(f"Building Copilot knowledge index ({mode})...")
        print(f"Source: {SESSION_STATE}")
        print(f"Output: {DB_PATH}")
        print()

        total_stats = {"checkpoints": 0, "research": 0, "files": 0, "plan": 0}

        for session_dir in sorted(SESSION_STATE.iterdir()):
            if not session_dir.is_dir():
                continue
            if not re.match(r"^[0-9a-f]{8}-", session_dir.name):
                continue

            stats = index_session(db, session_dir, incremental)
            indexed = sum(stats.values())
            if indexed > 0:
                print(f"  {session_dir.name[:8]}... indexed {indexed} docs "
                      f"(cp:{stats['checkpoints']} res:{stats['research']} "
                      f"files:{stats['files']} plan:{stats['plan']})")
            else:
                print(f"  {session_dir.name[:8]}... (no changes)" if incremental else
                      f"  {session_dir.name[:8]}... (no indexable content)")

            for k in total_stats:
                total_stats[k] += stats[k]

        db.commit()

        total = sum(total_stats.values())
        print(f"\nCopilot: Indexed {total} documents total "
              f"(cp:{total_stats['checkpoints']} res:{total_stats['research']} "
              f"files:{total_stats['files']} plan:{total_stats['plan']})")

    # Index Claude Code sessions (--claude or --all)
    if with_claude or all_sources:
        print(f"\n── Indexing Claude Code sessions ({mode}) ──")
        # Primary path: two-phase indexing via ClaudeProvider (Batch B).
        _run_two_phase_claude(db, incremental)

    show_stats(db)
    db.close()

    # Build embeddings (default: auto, skip with --no-embed)
    if with_embeddings:
        try:
            tools_dir = Path(__file__).parent
            sys.path.insert(0, str(tools_dir))
            from embed import build_embeddings, load_config, resolve_provider
            config = load_config()
            provider_config = resolve_provider(config)
            if provider_config:
                print("\n── Generating embeddings (auto) ──")
                build_embeddings()
            else:
                print("\n── Skipping embeddings (no API key configured) ──")
                print("  Run: python embed.py --setup")
        except ImportError:
            pass  # embed.py not available, silently skip
        except Exception as e:
            print(f"  Embedding error: {e}")


if __name__ == "__main__":
    main()
