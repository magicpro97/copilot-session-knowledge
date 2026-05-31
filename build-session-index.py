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
    python build-session-index.py --refresh-cost     # Backfill NULL cost columns
    python build-session-index.py --refresh-cost --limit N  # Backfill at most N sessions
"""

import concurrent.futures
import hashlib
import json
import os
import re
import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

MAX_WORKERS = int(os.environ.get("SK_INDEX_WORKERS", "4"))

# Fix Windows console encoding for Unicode output
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SESSION_STATE = Path.home() / ".copilot" / "session-state"
DB_PATH = Path(os.environ.get("SK_DB_PATH", str(SESSION_STATE / "knowledge.db"))).expanduser()
PROGRESS_PATH = DB_PATH.parent / "index-progress.json"
_progress_warning_emitted = False

CHECKPOINT_SECTIONS = ["overview", "history", "work_done", "technical_details", "important_files", "next_steps"]


def _write_progress(stage: str, **fields) -> None:
    """Write a small status file so long index builds are observable."""
    global _progress_warning_emitted
    try:
        PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "stage": stage,
            "pid": os.getpid(),
            "db_path": str(DB_PATH),
            "source": str(SESSION_STATE),
            "updated_at": datetime.now().isoformat(),
            **fields,
        }
        tmp = PROGRESS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(PROGRESS_PATH))
    except OSError as exc:
        if not _progress_warning_emitted:
            print(f"Warning: could not write index progress: {exc}", file=sys.stderr, flush=True)
            _progress_warning_emitted = True


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "").strip().lower())


def _stable_sha256(*parts) -> str:
    payload = "\0".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _document_stable_id(session_id: str, doc_type: str, seq: int, title: str) -> str:
    return _stable_sha256("document", session_id, doc_type, int(seq or 0), _normalize_title(title))


def _section_stable_id(document_stable_id: str, section_name: str) -> str:
    return _stable_sha256("section", document_stable_id, section_name or "")


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _default_local_replica_id() -> str:
    host = os.environ.get("HOSTNAME") or os.environ.get("COMPUTERNAME") or ""
    user = os.environ.get("USER") or os.environ.get("USERNAME") or ""
    return f"replica-{_stable_sha256('local-replica', host, user, str(Path.home()))[:16]}"


def _get_local_replica_id(db: sqlite3.Connection) -> str:
    try:
        row = db.execute("SELECT value FROM sync_state WHERE key='local_replica_id'").fetchone()
        current = str(row[0]) if row and row[0] else ""
        if current and current != "local":
            return current
        replica_id = _default_local_replica_id()
        db.execute(
            """
            INSERT INTO sync_state (key, value)
            VALUES ('local_replica_id', ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = datetime('now')
        """,
            (replica_id,),
        )
        db.execute(
            """
            INSERT INTO sync_metadata (key, value)
            VALUES ('local_replica_id', ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = datetime('now')
        """,
            (replica_id,),
        )
        return replica_id
    except Exception:
        return ""


def _enqueue_sync_op_fail_open(
    db: sqlite3.Connection,
    table_name: str,
    row_stable_id: str,
    row_payload: dict,
    op_type: str = "upsert",
):
    try:
        tools_dir = str(Path(__file__).resolve().parent)
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        from sync_enqueue import enqueue_sync_op_fail_open

        enqueue_sync_op_fail_open(db, table_name, row_stable_id, row_payload, op_type)
    except Exception:
        return


def _seed_sync_table_policies(db: sqlite3.Connection):
    rows = [
        ("sessions", "canonical", "id"),
        ("documents", "canonical", "stable_id"),
        ("sections", "canonical", "stable_id"),
        ("knowledge_entries", "canonical", "stable_id"),
        ("knowledge_relations", "canonical", "stable_id"),
        ("entity_relations", "canonical", "stable_id"),
        ("search_feedback", "canonical", "stable_id"),
        ("recall_events", "upload_only", ""),
        ("entry_recall_stats", "upload_only", ""),
        ("entry_recall_day_log", "upload_only", ""),
        ("entry_recall_query_log", "upload_only", ""),
        ("knowledge_fts", "local_only", ""),
        ("ke_fts", "local_only", ""),
        ("sessions_fts", "local_only", ""),
        ("event_offsets", "local_only", ""),
        ("embeddings", "local_only", ""),
        ("embedding_meta", "local_only", ""),
        ("tfidf_model", "local_only", ""),
    ]
    policy_sql = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sync_table_policies'"
    ).fetchone()
    needs_rebuild = policy_sql and "upload_only" not in (policy_sql[0] or "")
    if needs_rebuild:
        db.executescript("""
            CREATE TABLE sync_table_policies_new (
                table_name TEXT PRIMARY KEY,
                sync_scope TEXT NOT NULL CHECK(sync_scope IN ('canonical', 'local_only', 'upload_only')),
                stable_id_column TEXT DEFAULT ''
            );
            INSERT INTO sync_table_policies_new (table_name, sync_scope, stable_id_column)
            SELECT table_name, sync_scope, COALESCE(stable_id_column, '')
            FROM sync_table_policies;
            DROP TABLE sync_table_policies;
            ALTER TABLE sync_table_policies_new RENAME TO sync_table_policies;
        """)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS sync_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS sync_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS sync_txns (
            txn_id TEXT PRIMARY KEY,
            replica_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending', 'committed', 'failed')),
            created_at TEXT NOT NULL,
            committed_at TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS sync_ops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            txn_id TEXT NOT NULL,
            table_name TEXT NOT NULL,
            op_type TEXT NOT NULL CHECK(op_type IN ('insert', 'update', 'delete', 'upsert')),
            row_stable_id TEXT NOT NULL,
            row_payload TEXT NOT NULL,
            op_index INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(txn_id, op_index)
        );
        CREATE INDEX IF NOT EXISTS idx_sync_ops_txn ON sync_ops(txn_id);
        CREATE INDEX IF NOT EXISTS idx_sync_ops_table_row ON sync_ops(table_name, row_stable_id);
        CREATE TABLE IF NOT EXISTS sync_cursors (
            replica_id TEXT PRIMARY KEY,
            last_txn_id TEXT DEFAULT '',
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS sync_failures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            txn_id TEXT DEFAULT '',
            table_name TEXT DEFAULT '',
            row_stable_id TEXT DEFAULT '',
            error_code TEXT DEFAULT '',
            error_message TEXT DEFAULT '',
            failed_at TEXT NOT NULL,
            retry_count INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_sync_failures_txn ON sync_failures(txn_id);
        CREATE TABLE IF NOT EXISTS sync_table_policies (
            table_name TEXT PRIMARY KEY,
            sync_scope TEXT NOT NULL CHECK(sync_scope IN ('canonical', 'local_only', 'upload_only')),
            stable_id_column TEXT DEFAULT ''
        );
    """)
    db.executemany(
        """
        INSERT INTO sync_table_policies (table_name, sync_scope, stable_id_column)
        VALUES (?, ?, ?)
        ON CONFLICT(table_name) DO UPDATE SET
            sync_scope = excluded.sync_scope,
            stable_id_column = excluded.stable_id_column
    """,
        rows,
    )
    db.execute("""
        INSERT OR IGNORE INTO sync_metadata (key, value)
        VALUES ('local_replica_id', 'local')
    """)
    db.execute("""
        INSERT OR IGNORE INTO sync_state (key, value)
        VALUES ('local_replica_id', 'local')
    """)


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
            stable_id TEXT,
            file_path TEXT NOT NULL UNIQUE,
            file_hash TEXT,                -- SHA-256 for incremental updates (matches Rust sk watch, #336)
            size_bytes INTEGER DEFAULT 0,
            content_preview TEXT DEFAULT '',-- first 500 chars of content
            source TEXT DEFAULT 'copilot',
            indexed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            section_name TEXT NOT NULL,     -- 'overview', 'history', etc. or 'full' for non-checkpoints
            stable_id TEXT,
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
        CREATE INDEX IF NOT EXISTS idx_documents_stable_id ON documents(stable_id);
        CREATE INDEX IF NOT EXISTS idx_sections_doc ON sections(document_id);
        CREATE INDEX IF NOT EXISTS idx_sections_stable_id ON sections(stable_id);
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

    # Create sessions_fts for BM25 + column-scoped search (Batch C v8).
    # Column layout (ALL columns counted by snippet()/bm25(), UNINDEXED included):
    #   0=session_id (UNINDEXED), 1=title, 2=user_messages, 3=assistant_messages, 4=tool_names
    db.executescript("""
        CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5(
            session_id UNINDEXED,
            title,
            user_messages,
            assistant_messages,
            tool_names,
            tokenize='porter unicode61 remove_diacritics 2'
        );
    """)

    # Migrate existing databases: add source column if missing, then create indexes
    _migrate_add_source(db)
    _seed_sync_table_policies(db)
    _backfill_document_section_stable_ids(db)
    _enforce_stable_id_uniqueness(db)

    return db


def _migrate_add_source(db: sqlite3.Connection):
    """Add 'source' column and Phase 6/7 columns to existing tables (safe, idempotent)."""
    migrations = [
        ("sessions", "source", "TEXT DEFAULT 'copilot'"),
        ("documents", "source", "TEXT DEFAULT 'copilot'"),
        ("documents", "stable_id", "TEXT"),
        ("sections", "stable_id", "TEXT"),
        ("knowledge_entries", "source", "TEXT DEFAULT 'copilot'"),
        # Phase 6B: topic key, dedup, revision tracking
        ("knowledge_entries", "topic_key", "TEXT"),
        ("knowledge_entries", "revision_count", "INTEGER DEFAULT 1"),
        ("knowledge_entries", "content_hash", "TEXT"),
        # Phase 7: task-scoped recall + file/module surface
        ("knowledge_entries", "task_id", "TEXT DEFAULT ''"),
        ("knowledge_entries", "affected_files", "TEXT DEFAULT '[]'"),
        # Phase 3: provenance + code location metadata
        ("knowledge_entries", "source_section", "TEXT DEFAULT ''"),
        ("knowledge_entries", "source_file", "TEXT DEFAULT ''"),
        ("knowledge_entries", "start_line", "INTEGER DEFAULT 0"),
        ("knowledge_entries", "end_line", "INTEGER DEFAULT 0"),
        ("knowledge_entries", "code_language", "TEXT DEFAULT ''"),
        ("knowledge_entries", "code_snippet", "TEXT DEFAULT ''"),
        # Batch B v7: two-phase indexing columns on sessions
        ("sessions", "file_mtime", "REAL"),
        ("sessions", "indexed_at_r", "REAL"),
        ("sessions", "fts_indexed_at", "REAL"),
        ("sessions", "event_count_estimate", "INTEGER DEFAULT 0"),
        ("sessions", "file_size_bytes", "INTEGER DEFAULT 0"),
    ]
    _ALLOWED_TABLES = {"sessions", "documents", "sections", "knowledge_entries"}
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
        db.execute("CREATE INDEX IF NOT EXISTS idx_documents_stable_id ON documents(stable_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_sections_stable_id ON sections(stable_id)")
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
                source_stable_id TEXT DEFAULT '',
                target_stable_id TEXT DEFAULT '',
                relation_type TEXT NOT NULL,
                stable_id TEXT,
                confidence REAL DEFAULT 0.8,
                created_at TEXT,
                UNIQUE(source_id, target_id, relation_type)
            )
        """)
        for col, col_def in [
            ("source_stable_id", "TEXT DEFAULT ''"),
            ("target_stable_id", "TEXT DEFAULT ''"),
            ("stable_id", "TEXT"),
        ]:
            try:
                db.execute(f"ALTER TABLE knowledge_relations ADD COLUMN {col} {col_def}")
            except sqlite3.OperationalError:
                pass
        db.execute("CREATE INDEX IF NOT EXISTS idx_kr_source ON knowledge_relations(source_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_kr_target ON knowledge_relations(target_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_kr_source_stable ON knowledge_relations(source_stable_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_kr_target_stable ON knowledge_relations(target_stable_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_kr_stable_id ON knowledge_relations(stable_id)")
    except sqlite3.OperationalError:
        pass


def _dedupe_stable_rows(db: sqlite3.Connection, table: str):
    if table not in {"documents", "sections"}:
        return
    db.execute(f"""
        DELETE FROM {table}
        WHERE id IN (
            SELECT dupe.id
            FROM {table} dupe
            JOIN (
                SELECT stable_id, MIN(id) AS keep_id
                FROM {table}
                WHERE COALESCE(stable_id, '') != ''
                GROUP BY stable_id
                HAVING COUNT(*) > 1
            ) grouped ON grouped.stable_id = dupe.stable_id
            WHERE dupe.id != grouped.keep_id
        )
    """)


def _enforce_stable_id_uniqueness(db: sqlite3.Connection):
    has_table = lambda t: (
        db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (t,),
        ).fetchone()
        is not None
    )
    for table, index_name in [
        ("documents", "uq_documents_stable_id"),
        ("sections", "uq_sections_stable_id"),
    ]:
        if has_table(table):
            _dedupe_stable_rows(db, table)
            db.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table}(stable_id)")


def _backfill_document_section_stable_ids(db: sqlite3.Connection):
    try:
        for row in db.execute("""
            SELECT id, session_id, doc_type, seq, title, COALESCE(stable_id, '')
            FROM documents
        """).fetchall():
            doc_id, session_id, doc_type, seq, title, existing = row
            stable_id = _document_stable_id(session_id, doc_type, int(seq or 0), title)
            if existing != stable_id:
                db.execute("UPDATE documents SET stable_id = ? WHERE id = ?", (stable_id, doc_id))
                _enqueue_sync_op_fail_open(
                    db,
                    "documents",
                    stable_id,
                    {
                        "session_id": session_id,
                        "doc_type": doc_type,
                        "seq": int(seq or 0),
                        "title": title,
                        "stable_id": stable_id,
                    },
                )
        for row in db.execute("""
            SELECT s.id, d.stable_id, s.section_name, COALESCE(s.stable_id, '')
            FROM sections s
            JOIN documents d ON s.document_id = d.id
        """).fetchall():
            sec_id, document_stable_id, section_name, existing = row
            stable_id = _section_stable_id(document_stable_id, section_name)
            if existing != stable_id:
                db.execute("UPDATE sections SET stable_id = ? WHERE id = ?", (stable_id, sec_id))
                _enqueue_sync_op_fail_open(
                    db,
                    "sections",
                    stable_id,
                    {
                        "document_stable_id": document_stable_id,
                        "section_name": section_name,
                        "stable_id": stable_id,
                    },
                )
    except sqlite3.OperationalError:
        pass


def file_hash(path: Path) -> str:
    """Compute SHA-256 hash of file content for incremental updates.

    Uses SHA-256 to match the Rust native indexer (sk watch / session.rs).
    Python used MD5 historically; aligning here ensures cross-runtime cache hits
    so Python incremental runs benefit from prior Rust-indexed files (#336).
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ──────────────────────────────────────────────
# Batch B: two-phase indexing infrastructure
# ──────────────────────────────────────────────

# Noise filter: system events matching these patterns are dropped from FTS.
# Only applies when kind == "system" (B-BL-01: use kind not role).
# Scope cut: note kind is never indexed (contract §scope_cuts).
_NOISE_PATTERNS = [
    re.compile(r"^<context>", re.IGNORECASE),  # XML context blocks
    re.compile(r"^<system>", re.IGNORECASE),  # XML system blocks
    re.compile(r"you are claude", re.IGNORECASE),  # AI persona boilerplate
    re.compile(r"the assistant is claude", re.IGNORECASE),  # persona variant
    re.compile(r"^here are some instructions", re.IGNORECASE),  # instruction preamble
    re.compile(r"^this is a conversation", re.IGNORECASE),  # conversation header
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
    row = db.execute("SELECT file_mtime, fts_indexed_at FROM sessions WHERE id = ?", (session_id,)).fetchone()
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
    db.execute(
        """
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
    """,
        (
            session_id,
            path_str,
            source,
            file_mtime,
            file_size_bytes,
            event_count_estimate,
            now,
            datetime.now().isoformat(),
        ),
    )
    _enqueue_sync_op_fail_open(
        db,
        "sessions",
        session_id,
        {
            "id": session_id,
            "path": path_str,
            "source": source,
            "file_mtime": file_mtime,
            "file_size_bytes": file_size_bytes,
            "event_count_estimate": event_count_estimate,
            "indexed_at_r": now,
            "indexed_at": datetime.now().isoformat(),
        },
    )


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
      - DELETE FROM sessions_fts WHERE session_id = ?   (C: no duplicates)

    Iterates events, applies noise filter, inserts FTS rows and event_offsets.
    Aggregates user_msg / assistant_msg / tool_name content for sessions_fts.
    At end: sets fts_indexed_at = now() on sessions row.

    Returns number of FTS rows inserted.
    """
    # Ensure document row exists for FK (use session as a pseudo-document)
    doc_id = _get_or_create_session_document(db, session_id, session_meta)

    # B-BL-06 + C: DELETE before re-index to prevent FTS duplication on crash recovery.
    db.execute("DELETE FROM knowledge_fts WHERE session_id = ?", (session_id,))
    db.execute("DELETE FROM event_offsets WHERE session_id = ?", (session_id,))
    try:
        db.execute("DELETE FROM sessions_fts WHERE session_id = ?", (session_id,))
    except Exception:
        pass  # table may not exist yet on very old DBs

    # Batch C: per-session content accumulators for sessions_fts.
    # C-BL-01: use kind == "user_msg" / "assistant_msg", NOT role field (Event has no role).
    _user_parts: list = []
    _asst_parts: list = []
    _tool_names: set = set()

    inserted = 0
    total_events_seen = 0
    for event, byte_offset in provider.iter_events_with_offset(session_meta, from_event=0):
        total_events_seen += 1
        # Noise filter: skip system boilerplate and notes (scope cut).
        if _is_system_boilerplate(event):
            continue

        # Aggregate for sessions_fts (C-BL-01: kind-based, never role-based)
        if event.kind == "user_msg" and event.content:
            _user_parts.append(event.content)
        elif event.kind == "assistant_msg" and event.content:
            _asst_parts.append(event.content)
        elif event.kind in ("tool_call", "tool_result") and event.tool_name:
            _tool_names.add(event.tool_name)

        # Insert FTS row
        db.execute(
            """
            INSERT INTO knowledge_fts (title, section_name, content, doc_type, session_id, document_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            (
                session_meta.title or session_id[:8],
                event.kind,
                event.content,
                "claude-session" if session_meta.provider == "claude" else "copilot-session",
                session_id,
                doc_id,
            ),
        )

        # Insert event_offsets row (byte_offset may be -1 for non-Claude providers)
        db.execute(
            """
            INSERT OR REPLACE INTO event_offsets (session_id, event_id, byte_offset, file_mtime)
            VALUES (?, ?, ?, ?)
        """,
            (session_id, event.event_id, byte_offset, file_mtime),
        )

        inserted += 1

    # Populate sessions_fts — one row per session aggregating all event content.
    # Title: sessions.summary (fallback to session_id[:8]).
    _title = (session_meta.title or "").strip()
    if not _title:
        _row = db.execute("SELECT summary FROM sessions WHERE id = ?", (session_id,)).fetchone()
        _title = (_row[0] or "").strip() if _row else ""
    if not _title:
        _title = session_id[:8]

    try:
        db.execute(
            "INSERT INTO sessions_fts"
            " (session_id, title, user_messages, assistant_messages, tool_names)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                session_id,
                _title[:200],
                "\n\n".join(_user_parts),
                "\n\n".join(_asst_parts),
                " ".join(sorted(_tool_names)),
            ),
        )
    except Exception:
        pass  # sessions_fts table may not exist on very old DBs without v8 migration

    # Mark Phase 2 complete
    db.execute(
        "UPDATE sessions SET fts_indexed_at = ?, event_count_estimate = ? WHERE id = ?",
        (datetime.now().timestamp(), total_events_seen, session_id),
    )

    return inserted


def _get_or_create_session_document(db: sqlite3.Connection, session_id: str, session_meta) -> int:
    """Return document_id for the session pseudo-document, creating it if needed."""
    path_str = str(session_meta.path)
    row = db.execute("SELECT id FROM documents WHERE file_path = ?", (path_str,)).fetchone()
    if row:
        return row[0]

    doc_title = session_meta.title or session_id[:8]
    doc_stable_id = _document_stable_id(session_id, "claude-session", 0, doc_title)
    db.execute(
        """
        INSERT INTO documents
            (session_id, doc_type, seq, title, stable_id, file_path, size_bytes, indexed_at)
        VALUES (?, 'claude-session', 0, ?, ?, ?, ?, ?)
        ON CONFLICT(file_path) DO UPDATE SET
            title = excluded.title,
            stable_id = excluded.stable_id,
            size_bytes = excluded.size_bytes,
            indexed_at = excluded.indexed_at
    """,
        (
            session_id,
            doc_title,
            doc_stable_id,
            path_str,
            session_meta.path.stat().st_size if session_meta.path.exists() else 0,
            datetime.now().isoformat(),
        ),
    )
    _enqueue_sync_op_fail_open(
        db,
        "documents",
        doc_stable_id,
        {
            "session_id": session_id,
            "doc_type": "claude-session",
            "seq": 0,
            "title": doc_title,
            "stable_id": doc_stable_id,
            "file_path": path_str,
            "size_bytes": session_meta.path.stat().st_size if session_meta.path.exists() else 0,
            "indexed_at": datetime.now().isoformat(),
        },
    )
    return db.execute("SELECT id FROM documents WHERE file_path = ?", (path_str,)).fetchone()[0]


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
            entries.append(
                {
                    "seq": int(m.group(1)),
                    "title": m.group(2).strip(),
                    "file": m.group(3).strip(),
                }
            )
    return entries


def index_checkpoint(
    db: sqlite3.Connection, session_id: str, cp_path: Path, seq: int, title: str, incremental: bool
) -> bool:
    """Index a single checkpoint file. Returns True if indexed."""
    if not cp_path.exists():
        return False

    fhash = file_hash(cp_path)
    path_str = str(cp_path)

    if incremental:
        existing = db.execute("SELECT file_hash FROM documents WHERE file_path = ?", (path_str,)).fetchone()
        if existing and existing[0] == fhash:
            return False

    content = cp_path.read_text(encoding="utf-8", errors="ignore")
    preview = content[:500].replace("\n", " ")
    doc_stable_id = _document_stable_id(session_id, "checkpoint", seq, title)

    # Upsert document
    db.execute(
        """
        INSERT INTO documents (session_id, doc_type, seq, title, stable_id, file_path, file_hash, size_bytes, content_preview, indexed_at)
        VALUES (?, 'checkpoint', ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_path) DO UPDATE SET
            title=excluded.title, stable_id=excluded.stable_id,
            file_hash=excluded.file_hash, size_bytes=excluded.size_bytes,
            content_preview=excluded.content_preview, indexed_at=excluded.indexed_at
    """,
        (
            session_id,
            seq,
            title,
            doc_stable_id,
            path_str,
            fhash,
            cp_path.stat().st_size,
            preview,
            datetime.now().isoformat(),
        ),
    )
    _enqueue_sync_op_fail_open(
        db,
        "documents",
        doc_stable_id,
        {
            "session_id": session_id,
            "doc_type": "checkpoint",
            "seq": seq,
            "title": title,
            "stable_id": doc_stable_id,
            "file_path": path_str,
            "file_hash": fhash,
            "size_bytes": cp_path.stat().st_size,
            "content_preview": preview,
            "indexed_at": datetime.now().isoformat(),
        },
    )

    doc_id = db.execute("SELECT id FROM documents WHERE file_path = ?", (path_str,)).fetchone()[0]

    # Delete old sections and FTS entries
    db.execute("DELETE FROM knowledge_fts WHERE document_id = ?", (doc_id,))
    db.execute("DELETE FROM sections WHERE document_id = ?", (doc_id,))

    # Extract and index each section
    for section_name in CHECKPOINT_SECTIONS:
        section_content = extract_section(content, section_name)
        if section_content:
            db.execute(
                "INSERT INTO sections (document_id, section_name, stable_id, content) VALUES (?, ?, ?, ?)",
                (doc_id, section_name, _section_stable_id(doc_stable_id, section_name), section_content),
            )
            _enqueue_sync_op_fail_open(
                db,
                "sections",
                _section_stable_id(doc_stable_id, section_name),
                {
                    "document_stable_id": doc_stable_id,
                    "section_name": section_name,
                    "stable_id": _section_stable_id(doc_stable_id, section_name),
                    "content": section_content,
                },
            )
            db.execute(
                """
                INSERT INTO knowledge_fts (title, section_name, content, doc_type, session_id, document_id)
                VALUES (?, ?, ?, 'checkpoint', ?, ?)
            """,
                (title, section_name, section_content, session_id, doc_id),
            )

    return True


def index_generic_doc(
    db: sqlite3.Connection, session_id: str, doc_path: Path, doc_type: str, incremental: bool
) -> bool:
    """Index a research doc, artifact, or plan.md. Returns True if indexed."""
    if not doc_path.exists():
        return False

    fhash = file_hash(doc_path)
    path_str = str(doc_path)

    if incremental:
        existing = db.execute("SELECT file_hash FROM documents WHERE file_path = ?", (path_str,)).fetchone()
        if existing and existing[0] == fhash:
            return False

    content = doc_path.read_text(encoding="utf-8", errors="ignore")
    title = title_from_filename(doc_path.name)
    if doc_type == "plan":
        title = "Plan"
    preview = content[:500].replace("\n", " ")
    doc_stable_id = _document_stable_id(session_id, doc_type, 0, title)

    db.execute(
        """
        INSERT INTO documents (session_id, doc_type, seq, title, stable_id, file_path, file_hash, size_bytes, content_preview, indexed_at)
        VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_path) DO UPDATE SET
            title=excluded.title, stable_id=excluded.stable_id,
            file_hash=excluded.file_hash, size_bytes=excluded.size_bytes,
            content_preview=excluded.content_preview, indexed_at=excluded.indexed_at
    """,
        (
            session_id,
            doc_type,
            title,
            doc_stable_id,
            path_str,
            fhash,
            doc_path.stat().st_size,
            preview,
            datetime.now().isoformat(),
        ),
    )
    _enqueue_sync_op_fail_open(
        db,
        "documents",
        doc_stable_id,
        {
            "session_id": session_id,
            "doc_type": doc_type,
            "seq": 0,
            "title": title,
            "stable_id": doc_stable_id,
            "file_path": path_str,
            "file_hash": fhash,
            "size_bytes": doc_path.stat().st_size,
            "content_preview": preview,
            "indexed_at": datetime.now().isoformat(),
        },
    )

    doc_id = db.execute("SELECT id FROM documents WHERE file_path = ?", (path_str,)).fetchone()[0]

    # Delete old and re-index
    db.execute("DELETE FROM knowledge_fts WHERE document_id = ?", (doc_id,))
    db.execute("DELETE FROM sections WHERE document_id = ?", (doc_id,))

    # Store full content as single section
    db.execute(
        "INSERT INTO sections (document_id, section_name, stable_id, content) VALUES (?, 'full', ?, ?)",
        (doc_id, _section_stable_id(doc_stable_id, "full"), content),
    )
    _enqueue_sync_op_fail_open(
        db,
        "sections",
        _section_stable_id(doc_stable_id, "full"),
        {
            "document_stable_id": doc_stable_id,
            "section_name": "full",
            "stable_id": _section_stable_id(doc_stable_id, "full"),
            "content": content,
        },
    )
    db.execute(
        """
        INSERT INTO knowledge_fts (title, section_name, content, doc_type, session_id, document_id)
        VALUES (?, 'full', ?, ?, ?, ?)
    """,
        (title, content, doc_type, session_id, doc_id),
    )

    return True


def index_session(db: sqlite3.Connection, session_dir: Path, incremental: bool) -> dict:
    """Index all content in a session directory. Returns stats."""
    session_id = session_dir.name
    stats = {"checkpoints": 0, "research": 0, "files": 0, "plan": 0}

    # Ensure session row exists first (FK constraint)
    db.execute(
        """
        INSERT INTO sessions (id, path, summary, indexed_at)
        VALUES (?, ?, '', ?)
        ON CONFLICT(id) DO NOTHING
    """,
        (session_id, str(session_dir), datetime.now().isoformat()),
    )
    _enqueue_sync_op_fail_open(
        db,
        "sessions",
        session_id,
        {
            "id": session_id,
            "path": str(session_dir),
            "summary": "",
            "indexed_at": datetime.now().isoformat(),
        },
    )

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
    db.execute(
        """
        INSERT INTO sessions (id, path, summary, total_checkpoints, total_research, total_files, has_plan, indexed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            summary=excluded.summary, total_checkpoints=excluded.total_checkpoints,
            total_research=excluded.total_research, total_files=excluded.total_files,
            has_plan=excluded.has_plan, indexed_at=excluded.indexed_at
    """,
        (
            session_id,
            str(session_dir),
            summary,
            len(checkpoints),
            len(list(research_dir.glob("*.md"))) if research_dir.exists() else 0,
            len([f for f in files_dir.iterdir() if f.is_file() and f.suffix in (".md", ".txt")])
            if files_dir.exists()
            else 0,
            1 if plan_path.exists() and plan_path.stat().st_size > 50 else 0,
            datetime.now().isoformat(),
        ),
    )
    _enqueue_sync_op_fail_open(
        db,
        "sessions",
        session_id,
        {
            "id": session_id,
            "path": str(session_dir),
            "summary": summary,
            "total_checkpoints": len(checkpoints),
            "total_research": len(list(research_dir.glob("*.md"))) if research_dir.exists() else 0,
            "total_files": len([f for f in files_dir.iterdir() if f.is_file() and f.suffix in (".md", ".txt")])
            if files_dir.exists()
            else 0,
            "has_plan": 1 if plan_path.exists() and plan_path.stat().st_size > 50 else 0,
            "indexed_at": datetime.now().isoformat(),
        },
    )

    # Persist cost/token data from events.jsonl session.shutdown event
    try:
        cost_data = _extract_session_cost(session_dir)
        if cost_data:
            db.execute(
                """
                UPDATE sessions SET
                    cost_usd_est = ?,
                    total_input_tokens = ?,
                    total_output_tokens = ?
                WHERE id = ?
                """,
                (
                    cost_data["cost_usd_est"],
                    cost_data["total_input_tokens"],
                    cost_data["total_output_tokens"],
                    session_id,
                ),
            )
    except Exception:
        pass  # fail-open: cost columns may not exist on unrun migration

    return stats


# ---------------------------------------------------------------------------
# Parallel indexing helpers (Issue #840)
# ---------------------------------------------------------------------------

_db_lock = threading.Lock()


def _index_session_parallel(session_dir: Path, db_path: Path, incremental: bool) -> dict:
    """Process a single session directory in a thread pool worker.

    DB writes in index_session() are serialized via _db_lock. The FS reads
    inside index_session() (checkpoint parsing, file scanning) happen outside
    the lock's critical path when other threads hold it, providing modest
    parallelism for IO-bound workloads.
    """
    result: dict = {"path": str(session_dir), "stats": {}, "error": None}
    try:
        with _db_lock:
            conn = create_db(db_path)
            try:
                stats = index_session(conn, session_dir, incremental)
                conn.commit()
            finally:
                conn.close()
        result["stats"] = stats
    except Exception as exc:
        result["error"] = str(exc)
    return result


def index_sessions_parallel(
    session_dirs: list[Path],
    db_path: Path,
    incremental: bool,
    workers: int = MAX_WORKERS,
) -> list[dict]:
    """Index a list of session directories using a ThreadPoolExecutor.

    Falls back to sequential indexing if the executor itself raises.
    Progress is reported as ``sessions/s`` to stdout.
    Returns a list of result dicts (one per session).
    """
    if workers <= 1 or len(session_dirs) <= 1:
        # Sequential path — reuse a single DB connection
        conn = create_db(db_path)
        results: list[dict] = []
        try:
            for session_dir in session_dirs:
                try:
                    stats = index_session(conn, session_dir, incremental)
                    conn.commit()
                    results.append({"path": str(session_dir), "stats": stats, "error": None})
                except Exception as exc:
                    results.append({"path": str(session_dir), "stats": {}, "error": str(exc)})
        finally:
            conn.close()
        return results

    try:
        results = []
        total = len(session_dirs)
        t0 = time.monotonic()

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_dir = {
                executor.submit(_index_session_parallel, sd, db_path, incremental): sd for sd in session_dirs
            }
            for i, future in enumerate(concurrent.futures.as_completed(future_to_dir), 1):
                try:
                    result = future.result()
                except Exception as exc:
                    result = {"path": str(future_to_dir[future]), "stats": {}, "error": str(exc)}
                results.append(result)

                elapsed = time.monotonic() - t0
                rate = i / elapsed if elapsed > 0 else 0
                print(f"\r[index] {i}/{total} sessions ({rate:.1f}/s)", end="", flush=True)

        print()  # newline after progress
        return results

    except Exception as exc:
        # Fallback to sequential on executor error — reuse single connection
        print(f"\n[index] parallel executor error ({exc}), falling back to sequential", flush=True)
        conn = create_db(db_path)
        results = []
        try:
            for session_dir in session_dirs:
                try:
                    stats = index_session(conn, session_dir, incremental)
                    conn.commit()
                    results.append({"path": str(session_dir), "stats": stats, "error": None})
                except Exception as exc2:
                    results.append({"path": str(session_dir), "stats": {}, "error": str(exc2)})
        finally:
            conn.close()
        return results


def _extract_session_cost(session_dir: Path) -> dict | None:
    """Extract cost estimate from events.jsonl session.shutdown event.

    Reads modelMetrics from the last session.shutdown event and computes
    cost_usd_est by summing per-model costs using the same rate table as
    statusline.py.  Returns None when no shutdown event is found.
    """
    events_path = session_dir / "events.jsonl"
    if not events_path.exists():
        return None

    shutdown_data = None
    try:
        with open(events_path, "rb") as fh:
            for raw_line in fh:
                if b"session.shutdown" not in raw_line:
                    continue
                try:
                    ev = json.loads(raw_line.decode("utf-8", errors="replace"))
                    if ev.get("type") == "session.shutdown":
                        shutdown_data = ev.get("data") or {}
                except (json.JSONDecodeError, Exception):
                    continue
    except OSError:
        return None

    if not shutdown_data:
        return None

    model_metrics = shutdown_data.get("modelMetrics") or {}
    if not model_metrics:
        return None

    total_input = 0
    total_output = 0
    cost_usd_est = 0.0

    for model_id, metrics in model_metrics.items():
        usage = metrics.get("usage") or {}
        input_t = int(usage.get("inputTokens", 0))
        output_t = int(usage.get("outputTokens", 0))
        cache_read_t = int(usage.get("cacheReadTokens", 0))
        total_input += input_t
        total_output += output_t
        cost_usd_est += _cost_usd_for_model(model_id, input_t, cache_read_t, output_t)

    return {
        "cost_usd_est": round(cost_usd_est, 6),
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
    }


# Per-model rates in USD per 1M tokens — mirrors statusline.py _MODEL_RATES.
# Kept local to avoid cross-script imports (architecture convention).
_SESSION_MODEL_RATES: dict[str, dict[str, float]] = {
    "claude-sonnet-4.6": {"input": 3.00, "cached_input": 0.30, "output": 15.00},
    "claude-sonnet-4.5": {"input": 3.00, "cached_input": 0.30, "output": 15.00},
    "claude-sonnet-4": {"input": 3.00, "cached_input": 0.30, "output": 15.00},
    "claude-opus-4.7": {"input": 5.00, "cached_input": 0.50, "output": 25.00},
    "claude-opus-4.6": {"input": 5.00, "cached_input": 0.50, "output": 25.00},
    "claude-opus-4.5": {"input": 5.00, "cached_input": 0.50, "output": 25.00},
    "claude-haiku-4.5": {"input": 1.00, "cached_input": 0.10, "output": 5.00},
    "gpt-4.1": {"input": 2.00, "cached_input": 0.50, "output": 8.00},
    "gpt-4o": {"input": 2.00, "cached_input": 0.50, "output": 8.00},
    "gpt-5-mini": {"input": 0.25, "cached_input": 0.025, "output": 2.00},
    "gpt-5.2": {"input": 1.75, "cached_input": 0.175, "output": 14.00},
    "gpt-5.2-codex": {"input": 1.75, "cached_input": 0.175, "output": 14.00},
    "gpt-5.3-codex": {"input": 1.75, "cached_input": 0.175, "output": 14.00},
    "gpt-5.4": {"input": 2.50, "cached_input": 0.25, "output": 15.00},
    "gpt-5.4-mini": {"input": 0.75, "cached_input": 0.075, "output": 4.50},
    "gpt-5.4-nano": {"input": 0.20, "cached_input": 0.02, "output": 1.25},
    "gpt-5.5": {"input": 5.00, "cached_input": 0.50, "output": 30.00},
    "gemini-2.5-pro": {"input": 1.25, "cached_input": 0.125, "output": 10.00},
    "gemini-3-flash": {"input": 0.50, "cached_input": 0.05, "output": 3.00},
    "gemini-3.1-pro": {"input": 2.00, "cached_input": 0.20, "output": 12.00},
    "gemini-3.5-flash": {"input": 1.50, "cached_input": 0.15, "output": 9.00},
    "raptor-mini": {"input": 0.25, "cached_input": 0.025, "output": 2.00},
}
_TOKENS_PER_MILLION = 1_000_000
_DEFAULT_RATE = _SESSION_MODEL_RATES["claude-sonnet-4.6"]


def _cost_usd_for_model(model_id: str, total_input: int, cached_input: int, total_output: int) -> float:
    """Estimate cost in USD for one model's token usage."""
    mid = model_id.lower().strip()
    rate = _SESSION_MODEL_RATES.get(mid)
    if rate is None:
        for key, r in _SESSION_MODEL_RATES.items():
            if key in mid or mid in key:
                rate = r
                break
        else:
            rate = _DEFAULT_RATE
    uncached = max(total_input - cached_input, 0)
    input_usd = (uncached / _TOKENS_PER_MILLION) * rate["input"]
    cached_usd = (cached_input / _TOKENS_PER_MILLION) * rate["cached_input"]
    output_usd = (total_output / _TOKENS_PER_MILLION) * rate["output"]
    return input_usd + cached_usd + output_usd


def show_stats(db: sqlite3.Connection):
    """Print index statistics."""
    sessions = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    docs = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    sections = db.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
    fts_rows = db.execute("SELECT COUNT(*) FROM knowledge_fts").fetchone()[0]

    print(f"\n{'=' * 50}")
    print("  Knowledge Index Statistics")
    print(f"{'=' * 50}")
    print(f"  Sessions:    {sessions}")
    print(f"  Documents:   {docs}")
    print(f"  Sections:    {sections}")
    print(f"  FTS entries: {fts_rows}")
    print(f"  DB size:     {DB_PATH.stat().st_size / 1024:.1f} KB")
    print(f"{'=' * 50}")

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
        print(
            f"Claude (two-phase): {sessions_found} sessions scanned, "
            f"{phase1_count} Phase-1, {phase2_count} Phase-2 re-indexed"
        )


def _run_two_phase_copilot(db: sqlite3.Connection, incremental: bool) -> None:
    """Two-phase Copilot session indexing using CopilotProvider."""
    tools_dir = Path(__file__).parent
    sys.path.insert(0, str(tools_dir))
    try:
        from providers import CopilotProvider
    except ImportError as exc:
        print(f"  [two-phase] Cannot import CopilotProvider: {exc}")
        return

    provider = CopilotProvider()
    sessions_found = 0
    phase1_count = 0
    phase2_count = 0

    for session_meta in provider.list_sessions():
        sessions_found += 1
        try:
            file_size = sum(f.stat().st_size for f in session_meta.path.rglob("*") if f.is_file())
        except OSError:
            file_size = 0

        existing = db.execute(
            """
            SELECT event_count_estimate, total_checkpoints, total_research, total_files, has_plan
            FROM sessions WHERE id = ?
            """,
            (session_meta.id,),
        ).fetchone()
        if existing is not None:
            stored_event_count = int(existing[0] or 0)
            derived_event_count = (
                int(existing[1] or 0) + int(existing[2] or 0) + int(existing[3] or 0) + (1 if existing[4] else 0)
            )
            event_count_est = stored_event_count if stored_event_count > 0 else derived_event_count
        else:
            event_count_est = 0

        phase1_upsert_session(
            db,
            session_id=session_meta.id,
            path_str=str(session_meta.path),
            source=session_meta.provider,
            file_mtime=session_meta.mtime,
            file_size_bytes=file_size,
            event_count_estimate=event_count_est,
        )
        phase1_count += 1

        if incremental and should_skip_session(db, session_meta.id, session_meta.mtime):
            continue

        try:
            inserted = phase2_index_events(
                db,
                session_id=session_meta.id,
                file_mtime=session_meta.mtime,
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
        print("  No Copilot sessions found.")
    else:
        print(
            f"Copilot (two-phase): {sessions_found} sessions scanned, "
            f"{phase1_count} Phase-1, {phase2_count} Phase-2 re-indexed"
        )


def _parse_limit_arg() -> int | None:
    """Parse --limit N from sys.argv; returns int or None."""
    for i, arg in enumerate(sys.argv):
        if arg == "--limit" and i + 1 < len(sys.argv):
            try:
                return int(sys.argv[i + 1])
            except ValueError:
                return None
    return None


def _refresh_cost(db: sqlite3.Connection, limit: int | None = None) -> None:
    """Backfill cost_usd_est / token columns for sessions where they are NULL.

    For each session with cost_usd_est IS NULL, reads events.jsonl via
    _extract_session_cost() and updates the row.  Sessions without events.jsonl
    are counted as skipped.  Stops after *limit* successful refreshes when given.
    """
    rows = db.execute("SELECT id, path FROM sessions WHERE cost_usd_est IS NULL").fetchall()
    total = len(rows)
    refreshed = 0
    skipped = 0

    for session_id, path in rows:
        if limit is not None and refreshed >= limit:
            break
        if not path:
            skipped += 1
            continue
        session_dir = Path(path)
        cost_data = _extract_session_cost(session_dir)
        if not cost_data:
            skipped += 1
            continue
        db.execute(
            """
            UPDATE sessions SET
                cost_usd_est = ?,
                total_input_tokens = ?,
                total_output_tokens = ?
            WHERE id = ?
            """,
            (
                cost_data["cost_usd_est"],
                cost_data["total_input_tokens"],
                cost_data["total_output_tokens"],
                session_id,
            ),
        )
        refreshed += 1

    db.commit()
    print(f"Refreshed cost for {refreshed}/{total} sessions ({skipped} skipped — no events.jsonl)")


def main():
    incremental = "--incremental" in sys.argv
    stats_only = "--stats" in sys.argv
    with_embeddings = "--no-embed" not in sys.argv  # Auto-embed by default
    with_claude = "--claude" in sys.argv
    all_sources = "--all" in sys.argv
    refresh_cost = "--refresh-cost" in sys.argv
    limit = _parse_limit_arg()

    if not SESSION_STATE.exists():
        print(f"Error: Session state directory not found: {SESSION_STATE}")
        sys.exit(1)

    db = create_db(DB_PATH)

    if stats_only:
        show_stats(db)
        db.close()
        return

    if refresh_cost:
        _refresh_cost(db, limit=limit)
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
        session_dirs = [
            session_dir
            for session_dir in sorted(SESSION_STATE.iterdir())
            if session_dir.is_dir() and re.match(r"^[0-9a-f]{8}-", session_dir.name)
        ]
        _write_progress(
            "copilot-discovered",
            current=0,
            total=len(session_dirs),
            message=f"Discovered {len(session_dirs)} Copilot session directories",
        )
        workers = MAX_WORKERS
        print(f"Discovered {len(session_dirs)} Copilot session directories. (workers={workers})", flush=True)

        _write_progress("copilot-indexing", current=0, total=len(session_dirs), message="Parallel indexing started")
        db.close()  # close the shared connection — parallel workers open their own
        db = None

        parallel_results = index_sessions_parallel(session_dirs, DB_PATH, incremental, workers=workers)

        # Reopen for subsequent two-phase and stats
        db = create_db(DB_PATH)

        for res in parallel_results:
            stats = res.get("stats") or {}
            for k in total_stats:
                total_stats[k] += stats.get(k, 0)
            if res.get("error"):
                print(f"  ERROR {Path(res['path']).name[:8]}...: {res['error']}", flush=True)

        total = sum(total_stats.values())
        print(
            f"\nCopilot: Indexed {total} documents total "
            f"(cp:{total_stats['checkpoints']} res:{total_stats['research']} "
            f"files:{total_stats['files']} plan:{total_stats['plan']})"
        )
        _write_progress(
            "copilot-complete",
            current=len(session_dirs),
            total=len(session_dirs),
            message=f"Copilot indexing complete: {total} documents",
        )
        print("\n── Refreshing Copilot session metadata + FTS ──")
        _write_progress("copilot-two-phase", message="Refreshing Copilot session metadata and FTS")
        _run_two_phase_copilot(db, incremental)

    # Index Claude Code sessions (--claude or --all)
    if with_claude or all_sources:
        print(f"\n── Indexing Claude Code sessions ({mode}) ──")
        # Primary path: two-phase indexing via ClaudeProvider (Batch B).
        _write_progress("claude-two-phase", message="Indexing Claude Code sessions")
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
                _write_progress("embeddings", message="Generating embeddings")
                build_embeddings()
            else:
                print("\n── Skipping embeddings (no API key configured) ──")
                print("  Run: python embed.py --setup")
        except ImportError:
            pass  # embed.py not available, silently skip
        except Exception as e:
            print(f"  Embedding error: {e}")
    _write_progress("complete", message="Index build complete")


if __name__ == "__main__":
    main()
