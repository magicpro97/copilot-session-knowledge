#!/usr/bin/env python3
"""Versioned DB migration for session-knowledge tools."""

import ast
import hashlib
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")


def _default_db_path() -> str:
    return os.environ.get("SK_DB_PATH") or os.path.expanduser("~/.copilot/session-state/knowledge.db")


def _latest_declared_migration_version() -> int | None:
    """Read the local migration literal for help text without executing migrations."""
    try:
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not any(isinstance(target, ast.Name) and target.id == "MIGRATIONS" for target in node.targets):
                continue
            migrations = ast.literal_eval(node.value)
            versions = [int(item[0]) for item in migrations]
            return max(versions) if versions else None
    except (OSError, SyntaxError, ValueError, TypeError):
        return None
    return None


def _usage() -> str:
    latest = _latest_declared_migration_version()
    latest_line = (
        f"Latest declared migration: v{latest}" if latest is not None else "Latest declared migration: unknown"
    )
    return "\n".join(
        [
            "Usage: python migrate.py [DB_PATH] [--backup-only] [--backup-path PATH]",
            "",
            "Run schema migrations for the session-knowledge SQLite database.",
            "If DB_PATH is omitted, SK_DB_PATH or ~/.copilot/session-state/knowledge.db is used.",
            "",
            "Options:",
            "  --backup-only       Copy DB_PATH to a rollback backup and exit without migrating.",
            "  --backup-path PATH  Destination path for --backup-only; fails if PATH exists.",
            "  -h, --help          Show this help and exit without touching the database.",
            "",
            latest_line,
        ]
    )


def _parse_cli_args(argv: list[str]) -> tuple[str, bool, str | None]:
    db_path = None
    backup_only = False
    backup_path = None
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in {"-h", "--help"}:
            print(_usage())
            raise SystemExit(0)
        if arg == "--backup-only":
            backup_only = True
        elif arg == "--backup-path":
            index += 1
            if index >= len(argv):
                print("  [migrate] --backup-path requires a destination path", file=sys.stderr)
                raise SystemExit(2)
            backup_path = argv[index]
        elif arg.startswith("-"):
            print(f"  [migrate] Unknown option: {arg}", file=sys.stderr)
            print(_usage(), file=sys.stderr)
            raise SystemExit(2)
        elif db_path is None:
            db_path = arg
        else:
            print(f"  [migrate] Unexpected extra argument: {arg}", file=sys.stderr)
            print(_usage(), file=sys.stderr)
            raise SystemExit(2)
        index += 1

    if backup_path and not backup_only:
        print("  [migrate] --backup-path can only be used with --backup-only", file=sys.stderr)
        raise SystemExit(2)
    return db_path or _default_db_path(), backup_only, backup_path


def _create_backup_copy(db_path: str, backup_path: str | None = None) -> Path:
    source = Path(db_path).expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"database does not exist: {source}")
    if backup_path:
        destination = Path(backup_path).expanduser()
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        destination = source.with_name(f"{source.name}.backup-{stamp}")
    if destination.exists():
        raise FileExistsError(f"backup destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    src_conn = sqlite3.connect(str(source))
    dst_conn = sqlite3.connect(str(destination))
    backup_error = None
    try:
        src_conn.backup(dst_conn)
    except sqlite3.Error as exc:
        backup_error = exc
    finally:
        dst_conn.close()
        src_conn.close()
    if backup_error is not None:
        destination.unlink(missing_ok=True)
        raise backup_error

    verify_conn = sqlite3.connect(str(destination))
    verify_error = None
    try:
        row = verify_conn.execute("PRAGMA quick_check").fetchone()
        status = row[0] if row else "no quick_check result"
        if str(status).lower() != "ok":
            raise sqlite3.DatabaseError(f"backup quick_check returned {status!r}")
    except sqlite3.Error as exc:
        verify_error = exc
    finally:
        verify_conn.close()
    if verify_error is not None:
        destination.unlink(missing_ok=True)
        raise verify_error
    return destination


def _print_database_recovery_hint(db_path: str, error: str) -> None:
    print(f"  [migrate] Database check failed for {db_path}: {error}", file=sys.stderr)
    print(
        "  [migrate] Recovery hint: restore a known-good backup, or move the database aside "
        "and rerun migration to bootstrap a fresh schema. See "
        "docs/RESILIENCE-RUNBOOK.md#5-database-schema-backup-and-rollback.",
        file=sys.stderr,
    )


def _print_database_retry_hint(db_path: str, error: str) -> None:
    print(f"  [migrate] Database check failed for {db_path}: {error}", file=sys.stderr)
    print(
        "  [migrate] Recovery hint: database appears locked or busy; stop active session-knowledge "
        "writers such as watch/sync processes, then retry the migration.",
        file=sys.stderr,
    )


def _validate_database_or_exit(db: sqlite3.Connection, db_path: str) -> None:
    try:
        row = db.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.OperationalError as exc:
        db.close()
        message = str(exc).lower()
        if "locked" in message or "busy" in message:
            _print_database_retry_hint(db_path, str(exc))
        else:
            _print_database_recovery_hint(db_path, str(exc))
        raise SystemExit(1) from None
    except sqlite3.DatabaseError as exc:
        db.close()
        _print_database_recovery_hint(db_path, str(exc))
        raise SystemExit(1) from None

    status = row[0] if row else "no integrity_check result"
    if str(status).lower() != "ok":
        db.close()
        _print_database_recovery_hint(db_path, f"integrity_check returned {status!r}")
        raise SystemExit(1)


def _normalize_title(title: str) -> str:
    normalized = (title or "").strip().lower()
    return re.sub(r"\s+", " ", normalized)


def _stable_sha256(*parts) -> str:
    payload = "\0".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _default_local_replica_id() -> str:
    host = os.environ.get("HOSTNAME") or os.environ.get("COMPUTERNAME") or ""
    user = os.environ.get("USER") or os.environ.get("USERNAME") or ""
    return f"replica-{_stable_sha256('local-replica', host, user, os.path.expanduser('~'))[:16]}"


def _get_local_replica_id(db: sqlite3.Connection) -> str:
    for table in ("sync_state", "sync_metadata"):
        try:
            row = db.execute(f"SELECT value FROM {table} WHERE key='local_replica_id'").fetchone()
        except sqlite3.OperationalError:
            continue
        current = str(row[0]).strip() if row and row[0] else ""
        if current and current != "local":
            return current
    replica_id = _default_local_replica_id()
    for table in ("sync_state", "sync_metadata"):
        try:
            db.execute(
                f"""
                INSERT INTO {table} (key, value)
                VALUES ('local_replica_id', ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = datetime('now')
            """,
                (replica_id,),
            )
        except sqlite3.OperationalError:
            pass
    return replica_id or "local"


def _normalize_search_feedback_origin(origin_replica_id: str, local_replica_id: str) -> str:
    origin = (origin_replica_id or "").strip()
    if not origin or origin == "local":
        return local_replica_id or "local"
    return origin


def _seed_sync_table_policies(db: sqlite3.Connection):
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
    """)
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
    else:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS sync_table_policies (
                table_name TEXT PRIMARY KEY,
                sync_scope TEXT NOT NULL CHECK(sync_scope IN ('canonical', 'local_only', 'upload_only')),
                stable_id_column TEXT DEFAULT ''
            );
        """)

    policies = [
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
        ("entry_concept_tags", "local_only", ""),
        ("entry_dream_scores", "local_only", ""),
        ("file_annotations", "local_only", ""),
        ("project_registry", "canonical", "project_id"),
        ("code_index", "local_only", ""),
        ("code_fts", "local_only", ""),
    ]
    db.executemany(
        """
        INSERT INTO sync_table_policies (table_name, sync_scope, stable_id_column)
        VALUES (?, ?, ?)
        ON CONFLICT(table_name) DO UPDATE SET
            sync_scope = excluded.sync_scope,
            stable_id_column = excluded.stable_id_column
    """,
        policies,
    )
    db.execute("""
        INSERT OR IGNORE INTO sync_metadata (key, value)
        VALUES ('local_replica_id', 'local')
    """)
    db.execute("""
        INSERT OR IGNORE INTO sync_state (key, value)
        VALUES ('local_replica_id', 'local')
    """)


_BACKFILL_BATCH_SIZE = 1000

# ── Issue #357: batch FTS rebuild ────────────────────────────────────────────
_FTS_REBUILD_BATCH_SIZE = 500

# ── Issue #358: cache FTS schema detection ───────────────────────────────────
# Bump this string whenever the ke_fts DDL changes so old caches are invalidated.
_KE_FTS_SCHEMA_VERSION = "v3-porter"
_KE_FTS_SCHEMA_VERSION_KEY = "ke_fts_schema_version"


def _get_cached_ke_fts_version(db: sqlite3.Connection) -> str:
    """Return the cached ke_fts schema version stored in wakeup_config, or ''."""
    try:
        row = db.execute("SELECT value FROM wakeup_config WHERE key=?", (_KE_FTS_SCHEMA_VERSION_KEY,)).fetchone()
        return str(row[0]) if row else ""
    except sqlite3.OperationalError:
        return ""


def _set_cached_ke_fts_version(db: sqlite3.Connection, version: str) -> None:
    """Persist the ke_fts schema version in wakeup_config for future cache hits."""
    try:
        db.execute(
            """
            INSERT INTO wakeup_config (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                updated_at = datetime('now')
            """,
            (_KE_FTS_SCHEMA_VERSION_KEY, version),
        )
    except sqlite3.OperationalError:
        pass


def _ke_fts_needs_rebuild(db: sqlite3.Connection) -> bool:
    """Return True when ke_fts must be rebuilt.

    Fast path (#358): if wakeup_config records the current schema version, skip
    the sqlite_master query entirely.
    """
    if _get_cached_ke_fts_version(db) == _KE_FTS_SCHEMA_VERSION:
        return False
    fts_row = db.execute("SELECT sql FROM sqlite_master WHERE name='ke_fts'").fetchone()
    if not fts_row:
        return False
    fts_def = fts_row[0] or ""
    needs = (
        "wing" not in fts_def
        or "facts" not in fts_def
        or "error_type" not in fts_def
        or "root_cause" not in fts_def
        or "porter" not in fts_def  # issue #373
    )
    return needs


def _rebuild_ke_fts_batched(db: sqlite3.Connection, new_ddl: str) -> None:
    """Rebuild ke_fts using batched inserts to avoid long write locks (#357).

    Uses BEGIN EXCLUSIVE so the DROP→RENAME is atomic; prevents FTS permanent
    loss if watch-sessions holds a read transaction.
    """
    has_table = (
        db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_entries'").fetchone() is not None
    )
    if not has_table:
        return

    db.execute("BEGIN EXCLUSIVE")
    try:
        db.execute("DROP TABLE IF EXISTS ke_fts_new")
        db.execute(new_ddl.replace("ke_fts", "ke_fts_new", 1))
        # Batched INSERT (#357)
        cur = db.execute(
            """
            SELECT id, title, content, tags, category,
                   COALESCE(wing,''), COALESCE(room,''),
                   COALESCE(facts,'[]'), COALESCE(error_type,''),
                   COALESCE(root_cause,'')
            FROM knowledge_entries
            """
        )
        while True:
            batch = cur.fetchmany(_FTS_REBUILD_BATCH_SIZE)
            if not batch:
                break
            db.executemany(
                """
                INSERT INTO ke_fts_new(rowid, title, content, tags, category,
                    wing, room, facts, error_type, root_cause)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                batch,
            )
        db.execute("DROP TABLE IF EXISTS ke_fts")
        db.execute("ALTER TABLE ke_fts_new RENAME TO ke_fts")
        db.execute("COMMIT")
    except Exception:
        try:
            db.execute("ROLLBACK")
        except Exception:
            pass
        try:
            db.execute("DROP TABLE IF EXISTS ke_fts_new")
        except Exception:
            pass
        raise


# ── Issue #392: chunked WAL checkpoint scheduling ────────────────────────────


def _wal_frame_count(wal_path: str, page_size: int) -> int:
    """Return the number of frames in a WAL file based on its size.

    WAL layout: 32-byte file header followed by frames of (24-byte frame
    header + page_size bytes).  Returns 0 if the file does not exist, is
    empty, or is smaller than the WAL header.
    """
    try:
        size = os.path.getsize(wal_path)
    except OSError:
        return 0
    if size <= 32 or page_size <= 0:
        return 0
    return (size - 32) // (24 + page_size)


def schedule_wal_checkpoint(db: sqlite3.Connection, threshold_pages: int = 1000) -> bool:
    """Run a PASSIVE WAL checkpoint when the WAL has grown past threshold_pages.

    Returns True if a checkpoint was attempted, False if below threshold or
    WAL mode is not active.  Uses PASSIVE mode so it never blocks writers.

    The threshold gate is evaluated *before* issuing PRAGMA wal_checkpoint so
    that hot-path callers with a busy WAL never pay the checkpoint cost when
    the WAL is still small.
    """
    try:
        mode_row = db.execute("PRAGMA journal_mode").fetchone()
        if not mode_row or str(mode_row[0]).lower() != "wal":
            return False
        # Gate on actual WAL size before running the checkpoint.
        if threshold_pages > 0:
            db_list = db.execute("PRAGMA database_list").fetchall()
            db_path = next((row[2] for row in db_list if row[1] == "main" and row[2]), None)
            if db_path:
                page_size_row = db.execute("PRAGMA page_size").fetchone()
                page_size = page_size_row[0] if page_size_row else 4096
                if _wal_frame_count(db_path + "-wal", page_size) < threshold_pages:
                    return False
            # db_path is empty for in-memory DBs — WAL mode is not reachable
            # for :memory: so control flow never reaches here in practice.
        db.execute("PRAGMA wal_checkpoint(PASSIVE)")
        return True
    except sqlite3.OperationalError:
        return False


# ── Issue #370: embedding dimension mismatch detection ───────────────────────


def detect_embedding_dimension_mismatch(db: sqlite3.Connection) -> list[dict]:
    """Return a list of mismatch records when stored embeddings use different dims.

    Each record has keys: source_type, model, stored_dimensions, provider.
    An empty list means no mismatch (or no embeddings table present).
    """
    mismatches: list[dict] = []
    try:
        has_emb = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='embeddings'").fetchone()
        if not has_emb:
            return mismatches
        rows = db.execute(
            """
            SELECT source_type, provider, model, dimensions, COUNT(*) as n
            FROM embeddings
            GROUP BY source_type, provider, model, dimensions
            """
        ).fetchall()
        # Group by (source_type, provider, model) — if more than one dim value exists,
        # or if it differs from embedding_meta's recorded configured dimension,
        # we have a mismatch.
        dim_map: dict[tuple, list[int]] = {}
        for row in rows:
            key = (str(row[0]), str(row[1]), str(row[2]))
            dim_map.setdefault(key, []).append(int(row[3]))
        for (source_type, provider, model), dims in dim_map.items():
            if len(set(dims)) > 1:
                mismatches.append(
                    {
                        "source_type": source_type,
                        "provider": provider,
                        "model": model,
                        "stored_dimensions": dims,
                        "issue": "mixed_dimensions",
                    }
                )
        # Also check against configured dimension in embedding_meta
        try:
            meta_row = db.execute("SELECT value FROM embedding_meta WHERE key='configured_dimensions'").fetchone()
            if meta_row and meta_row[0]:
                configured = int(meta_row[0])
                for (source_type, provider, model), dims in dim_map.items():
                    for d in set(dims):
                        if d != configured:
                            mismatches.append(
                                {
                                    "source_type": source_type,
                                    "provider": provider,
                                    "model": model,
                                    "stored_dimensions": d,
                                    "configured_dimensions": configured,
                                    "issue": "dimension_config_mismatch",
                                }
                            )
        except sqlite3.OperationalError:
            pass
    except sqlite3.OperationalError:
        pass
    return mismatches


def _backfill_stable_ids(db: sqlite3.Connection):
    has_table = lambda t: (
        db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (t,),
        ).fetchone()
        is not None
    )

    if has_table("documents"):
        _cur = db.execute("""
            SELECT id, session_id, doc_type, seq, title, COALESCE(stable_id, '')
            FROM documents
        """)
        while True:
            _batch = _cur.fetchmany(_BACKFILL_BATCH_SIZE)
            if not _batch:
                break
            for row in _batch:
                did, session_id, doc_type, seq, title, existing = row
                stable = _stable_sha256("document", session_id, doc_type, int(seq or 0), _normalize_title(title))
                if existing != stable:
                    db.execute("UPDATE documents SET stable_id = ? WHERE id = ?", (stable, did))

    if has_table("sections") and has_table("documents"):
        _cur = db.execute("""
            SELECT s.id, d.stable_id, s.section_name, COALESCE(s.stable_id, '')
            FROM sections s
            JOIN documents d ON s.document_id = d.id
            WHERE COALESCE(d.stable_id, '') != ''
        """)
        while True:
            _batch = _cur.fetchmany(_BACKFILL_BATCH_SIZE)
            if not _batch:
                break
            for row in _batch:
                sid, document_stable_id, section_name, existing = row
                stable = _stable_sha256("section", document_stable_id, section_name or "")
                if existing != stable:
                    db.execute("UPDATE sections SET stable_id = ? WHERE id = ?", (stable, sid))

    if has_table("knowledge_entries"):
        _cur = db.execute("""
            SELECT id, session_id, category, title, COALESCE(topic_key, ''), COALESCE(stable_id, '')
            FROM knowledge_entries
        """)
        while True:
            _batch = _cur.fetchmany(_BACKFILL_BATCH_SIZE)
            if not _batch:
                break
            for row in _batch:
                kid, session_id, category, title, topic_key, existing = row
                stable = _stable_sha256("knowledge", session_id, category, title or "", topic_key)
                if existing != stable:
                    db.execute("UPDATE knowledge_entries SET stable_id = ? WHERE id = ?", (stable, kid))

    if has_table("knowledge_relations") and has_table("knowledge_entries"):
        _cur = db.execute("""
            SELECT kr.id,
                   kr.source_id,
                   kr.target_id,
                   kr.relation_type,
                   COALESCE(kr.source_stable_id, ''),
                   COALESCE(kr.target_stable_id, ''),
                   COALESCE(kr.stable_id, ''),
                   COALESCE(s.stable_id, ''),
                   COALESCE(t.stable_id, '')
            FROM knowledge_relations kr
            LEFT JOIN knowledge_entries s ON kr.source_id = s.id
            LEFT JOIN knowledge_entries t ON kr.target_id = t.id
        """)
        while True:
            _batch = _cur.fetchmany(_BACKFILL_BATCH_SIZE)
            if not _batch:
                break
            for row in _batch:
                kr_id, _, _, relation_type, src_existing, tgt_existing, existing, src_sid, tgt_sid = row
                if not src_sid or not tgt_sid:
                    continue
                stable = _stable_sha256("knowledge_relation", src_sid, tgt_sid, relation_type or "")
                if src_existing != src_sid or tgt_existing != tgt_sid or existing != stable:
                    db.execute(
                        """
                        UPDATE knowledge_relations
                        SET source_stable_id = ?, target_stable_id = ?, stable_id = ?
                        WHERE id = ?
                    """,
                        (src_sid, tgt_sid, stable, kr_id),
                    )

    if has_table("entity_relations"):
        _cur = db.execute("""
            SELECT id, subject, predicate, object, COALESCE(stable_id, '')
            FROM entity_relations
        """)
        while True:
            _batch = _cur.fetchmany(_BACKFILL_BATCH_SIZE)
            if not _batch:
                break
            for row in _batch:
                er_id, subject, predicate, obj, existing = row
                stable = _stable_sha256("entity_relation", subject or "", predicate or "", obj or "")
                if existing != stable:
                    db.execute("UPDATE entity_relations SET stable_id = ? WHERE id = ?", (stable, er_id))

    if has_table("search_feedback"):
        local_replica_id = _get_local_replica_id(db)
        _cur = db.execute("""
            SELECT id, created_at, result_kind, result_id, verdict, query,
                   COALESCE(origin_replica_id, ''), COALESCE(stable_id, '')
            FROM search_feedback
        """)
        while True:
            _batch = _cur.fetchmany(_BACKFILL_BATCH_SIZE)
            if not _batch:
                break
            for row in _batch:
                sf_id, created_at, result_kind, result_id, verdict, query, origin_replica_id, existing = row
                origin = _normalize_search_feedback_origin(origin_replica_id, local_replica_id)
                stable = _stable_sha256(
                    "search_feedback",
                    created_at or "",
                    result_kind or "",
                    result_id or "",
                    verdict if verdict is not None else "",
                    query or "",
                    origin,
                )
                if existing != stable or origin_replica_id != origin:
                    db.execute(
                        """
                        UPDATE search_feedback
                        SET origin_replica_id = ?, stable_id = ?
                        WHERE id = ?
                    """,
                        (origin, stable, sf_id),
                    )


def _dedupe_stable_rows(db: sqlite3.Connection, table: str):
    if table not in {
        "documents",
        "sections",
        "knowledge_entries",
        "knowledge_relations",
        "entity_relations",
        "search_feedback",
    }:
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

    index_specs = [
        ("documents", "uq_documents_stable_id"),
        ("sections", "uq_sections_stable_id"),
        ("knowledge_entries", "uq_knowledge_entries_stable_id"),
        ("knowledge_relations", "uq_knowledge_relations_stable_id"),
        ("entity_relations", "uq_entity_relations_stable_id"),
        ("search_feedback", "uq_search_feedback_stable_id"),
    ]
    for table, index_name in index_specs:
        if not has_table(table):
            continue
        _dedupe_stable_rows(db, table)
        db.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table}(stable_id)")


def _repair_legacy_priority_collision(db: sqlite3.Connection):
    legacy_row = db.execute("SELECT 1 FROM schema_version WHERE version=22 AND name='file_annotations'").fetchone()
    if not legacy_row:
        return False, False

    db.execute("SAVEPOINT repair_priority")
    try:
        ke_cols = {row[1] for row in db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
        repaired = False
        if "priority" not in ke_cols:
            for repair_sql in (
                "ALTER TABLE knowledge_entries ADD COLUMN priority TEXT DEFAULT 'P2'",
                "CREATE INDEX IF NOT EXISTS idx_ke_priority ON knowledge_entries(priority)",
            ):
                try:
                    db.execute(repair_sql)
                except sqlite3.OperationalError as e:
                    if "duplicate" in str(e).lower() or "already exists" in str(e).lower():
                        pass
                    else:
                        raise
            repaired = True

        renamed = (
            db.execute(
                "UPDATE schema_version SET name='priority' WHERE version=22 AND name='file_annotations'"
            ).rowcount
            > 0
        )
        db.execute("RELEASE SAVEPOINT repair_priority")
        if repaired or renamed:
            db.commit()
        return repaired, renamed
    except Exception:
        try:
            db.execute("ROLLBACK TO SAVEPOINT repair_priority")
            db.execute("RELEASE SAVEPOINT repair_priority")
        except Exception:
            pass
        raise


def _ensure_base_schema(db: sqlite3.Connection):
    """Bootstrap the full base schema for a brand-new knowledge DB.

    Fresh project-local DBs do not have an old migration history to upgrade from, so
    they need the current base tables before the versioned ALTER/CREATE steps run.
    Existing databases are unaffected because every statement is idempotent.
    """
    db.executescript("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            migrated_at TEXT DEFAULT (datetime('now')),
            name TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            summary TEXT DEFAULT '',
            total_checkpoints INTEGER DEFAULT 0,
            total_research INTEGER DEFAULT 0,
            total_files INTEGER DEFAULT 0,
            has_plan INTEGER DEFAULT 0,
            source TEXT DEFAULT 'copilot',
            indexed_at TEXT,
            file_mtime REAL,
            indexed_at_r REAL,
            fts_indexed_at REAL,
            event_count_estimate INTEGER DEFAULT 0,
            file_size_bytes INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            doc_type TEXT NOT NULL,
            seq INTEGER DEFAULT 0,
            title TEXT NOT NULL,
            stable_id TEXT,
            file_path TEXT NOT NULL UNIQUE,
            file_hash TEXT,
            size_bytes INTEGER DEFAULT 0,
            content_preview TEXT DEFAULT '',
            source TEXT DEFAULT 'copilot',
            indexed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            section_name TEXT NOT NULL,
            stable_id TEXT,
            content TEXT NOT NULL,
            UNIQUE(document_id, section_name)
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
            title,
            section_name,
            content,
            doc_type,
            session_id UNINDEXED,
            document_id UNINDEXED,
            tokenize='unicode61 remove_diacritics 2'
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5(
            session_id UNINDEXED,
            title,
            user_messages,
            assistant_messages,
            tool_names,
            tokenize='porter unicode61 remove_diacritics 2'
        );
        CREATE TABLE IF NOT EXISTS event_offsets (
            session_id TEXT NOT NULL,
            event_id INTEGER NOT NULL,
            byte_offset INTEGER NOT NULL,
            file_mtime REAL NOT NULL,
            PRIMARY KEY (session_id, event_id)
        );

        CREATE TABLE IF NOT EXISTS knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            document_id INTEGER,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            stable_id TEXT,
            content TEXT NOT NULL,
            tags TEXT DEFAULT '',
            confidence REAL DEFAULT 1.0,
            occurrence_count INTEGER DEFAULT 1,
            first_seen TEXT,
            last_seen TEXT,
            source TEXT DEFAULT 'copilot',
            topic_key TEXT,
            revision_count INTEGER DEFAULT 1,
            content_hash TEXT,
            wing TEXT DEFAULT '',
            room TEXT DEFAULT '',
            facts TEXT DEFAULT '[]',
            est_tokens INTEGER DEFAULT 0,
            task_id TEXT DEFAULT '',
            affected_files TEXT DEFAULT '[]',
            source_section TEXT DEFAULT '',
            source_file TEXT DEFAULT '',
            start_line INTEGER DEFAULT 0,
            end_line INTEGER DEFAULT 0,
            code_language TEXT DEFAULT '',
            code_snippet TEXT DEFAULT '',
            error_type TEXT DEFAULT '',
            root_cause TEXT DEFAULT '',
            severity TEXT DEFAULT 'medium',
            is_resolved INTEGER DEFAULT 0,
            fix_steps TEXT DEFAULT '',
            prevention_hook TEXT DEFAULT '',
            recurrence_after_briefing INTEGER DEFAULT 0,
            valence TEXT DEFAULT '',
            intensity REAL DEFAULT 0.5,
            priority TEXT DEFAULT 'P2',
            project_id TEXT DEFAULT '',
            UNIQUE(category, title, session_id)
        );

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
        );

        CREATE TABLE IF NOT EXISTS entity_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object TEXT NOT NULL,
            stable_id TEXT,
            noted_at TEXT DEFAULT (datetime('now')),
            session_id TEXT DEFAULT '',
            UNIQUE(subject, predicate, object)
        );

        CREATE TABLE IF NOT EXISTS search_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT,
            result_id TEXT,
            result_kind TEXT,
            verdict INTEGER NOT NULL CHECK(verdict IN (-1,0,1)),
            comment TEXT,
            user_agent TEXT,
            created_at TEXT NOT NULL,
            origin_replica_id TEXT DEFAULT 'local',
            stable_id TEXT
        );

        CREATE TABLE IF NOT EXISTS wakeup_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS ke_fts USING fts5(
            title,
            content,
            tags,
            category,
            wing,
            room,
            facts,
            error_type,
            root_cause,
            tokenize='porter unicode61 remove_diacritics 2'
        );

        CREATE TABLE IF NOT EXISTS project_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL DEFAULT '',
            repo_root TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
    """)

    index_statements = [
        "CREATE INDEX IF NOT EXISTS idx_documents_session ON documents(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_documents_type ON documents(doc_type)",
        "CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source)",
        "CREATE INDEX IF NOT EXISTS idx_documents_stable_id ON documents(stable_id)",
        "CREATE INDEX IF NOT EXISTS idx_sections_doc ON sections(document_id)",
        "CREATE INDEX IF NOT EXISTS idx_sections_stable_id ON sections(stable_id)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_source ON sessions(source)",
        "CREATE INDEX IF NOT EXISTS idx_event_offsets_session ON event_offsets(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_ke_category ON knowledge_entries(category)",
        "CREATE INDEX IF NOT EXISTS idx_ke_session ON knowledge_entries(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_ke_source ON knowledge_entries(source)",
        "CREATE INDEX IF NOT EXISTS idx_ke_topic ON knowledge_entries(topic_key)",
        "CREATE INDEX IF NOT EXISTS idx_ke_hash ON knowledge_entries(content_hash)",
        "CREATE INDEX IF NOT EXISTS idx_ke_task ON knowledge_entries(task_id)",
        "CREATE INDEX IF NOT EXISTS idx_ke_stable_id ON knowledge_entries(stable_id)",
        "CREATE INDEX IF NOT EXISTS idx_ke_intensity ON knowledge_entries(intensity DESC)",
        "CREATE INDEX IF NOT EXISTS idx_ke_priority ON knowledge_entries(priority)",
        "CREATE INDEX IF NOT EXISTS idx_ke_project_id ON knowledge_entries(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_kr_source ON knowledge_relations(source_id)",
        "CREATE INDEX IF NOT EXISTS idx_kr_target ON knowledge_relations(target_id)",
        "CREATE INDEX IF NOT EXISTS idx_kr_source_stable ON knowledge_relations(source_stable_id)",
        "CREATE INDEX IF NOT EXISTS idx_kr_target_stable ON knowledge_relations(target_stable_id)",
        "CREATE INDEX IF NOT EXISTS idx_kr_stable_id ON knowledge_relations(stable_id)",
        "CREATE INDEX IF NOT EXISTS idx_er_subject ON entity_relations(subject)",
        "CREATE INDEX IF NOT EXISTS idx_er_object ON entity_relations(object)",
        "CREATE INDEX IF NOT EXISTS idx_er_stable_id ON entity_relations(stable_id)",
        "CREATE INDEX IF NOT EXISTS idx_sf_query ON search_feedback(query)",
        "CREATE INDEX IF NOT EXISTS idx_sf_created ON search_feedback(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_sf_stable_id ON search_feedback(stable_id)",
        "CREATE INDEX IF NOT EXISTS idx_sf_origin_replica ON search_feedback(origin_replica_id)",
        "CREATE INDEX IF NOT EXISTS idx_pr_project_id ON project_registry(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_pr_repo_root ON project_registry(repo_root)",
    ]
    for sql in index_statements:
        try:
            db.execute(sql)
        except sqlite3.OperationalError:
            pass


if __name__ == "__main__":
    db_path, backup_only, backup_path = _parse_cli_args(sys.argv[1:])
    if backup_only:
        try:
            created = _create_backup_copy(db_path, backup_path)
        except (FileNotFoundError, FileExistsError, OSError, sqlite3.Error) as exc:
            print(f"  [migrate] Backup failed: {exc}", file=sys.stderr)
            raise SystemExit(1) from None
        print(f"  [migrate] Backup created: {created}")
        raise SystemExit(0)

    try:
        db = sqlite3.connect(db_path)
    except sqlite3.Error as exc:
        _print_database_recovery_hint(db_path, str(exc))
        raise SystemExit(1) from None
    _validate_database_or_exit(db, db_path)
    _ensure_base_schema(db)
    try:
        db.execute("ALTER TABLE schema_version ADD COLUMN name TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    current = db.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0
    MIGRATIONS = [
        (
            2,
            "add_wing_room",
            [
                "ALTER TABLE knowledge_entries ADD COLUMN wing TEXT DEFAULT ''",
                "ALTER TABLE knowledge_entries ADD COLUMN room TEXT DEFAULT ''",
            ],
        ),
        (
            3,
            "entity_relations",
            [
                "CREATE TABLE IF NOT EXISTS entity_relations (id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT NOT NULL, predicate TEXT NOT NULL, object TEXT NOT NULL, noted_at TEXT DEFAULT (datetime('now')), session_id TEXT DEFAULT '', UNIQUE(subject, predicate, object))",
                "CREATE INDEX IF NOT EXISTS idx_er_subject ON entity_relations(subject)",
                "CREATE INDEX IF NOT EXISTS idx_er_object ON entity_relations(object)",
            ],
        ),
        (
            4,
            "wakeup_config",
            [
                "CREATE TABLE IF NOT EXISTS wakeup_config (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT DEFAULT (datetime('now')))",
            ],
        ),
        (
            5,
            "add_facts_column",
            [
                "ALTER TABLE knowledge_entries ADD COLUMN facts TEXT DEFAULT '[]'",
            ],
        ),
        (
            6,
            "add_est_tokens_column",
            [
                "ALTER TABLE knowledge_entries ADD COLUMN est_tokens INTEGER DEFAULT 0",
                "UPDATE knowledge_entries SET est_tokens = LENGTH(COALESCE(title,'') || ' ' || COALESCE(content,'')) / 4 WHERE est_tokens = 0",
            ],
        ),
        # v7: Batch B — two-phase indexing.
        # B-BL-07: CREATE TABLE IF NOT EXISTS sessions first so ALTERs don't fail on fresh DB.
        # B-BL-02: event_offsets.event_id is INTEGER NOT NULL (not TEXT).
        # B-BL-05: event_offsets has file_mtime REAL column.
        (
            7,
            "two_phase_indexing",
            [
                # Guard: ensure sessions table exists with current schema before ALTERs.
                """CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                summary TEXT DEFAULT '',
                total_checkpoints INTEGER DEFAULT 0,
                total_research INTEGER DEFAULT 0,
                total_files INTEGER DEFAULT 0,
                has_plan INTEGER DEFAULT 0,
                source TEXT DEFAULT 'copilot',
                indexed_at TEXT
            )""",
                # Add new Phase-1 / Phase-2 tracking columns (idempotent: runner catches 'duplicate').
                "ALTER TABLE sessions ADD COLUMN file_mtime REAL",
                "ALTER TABLE sessions ADD COLUMN indexed_at_r REAL",
                "ALTER TABLE sessions ADD COLUMN fts_indexed_at REAL",
                "ALTER TABLE sessions ADD COLUMN event_count_estimate INTEGER DEFAULT 0",
                "ALTER TABLE sessions ADD COLUMN file_size_bytes INTEGER DEFAULT 0",
                # event_offsets: byte-offset seek table.
                # event_id INTEGER NOT NULL (B-BL-02); file_mtime REAL (B-BL-05).
                """CREATE TABLE IF NOT EXISTS event_offsets (
                session_id TEXT NOT NULL,
                event_id INTEGER NOT NULL,
                byte_offset INTEGER NOT NULL,
                file_mtime REAL NOT NULL,
                PRIMARY KEY (session_id, event_id)
            )""",
                "CREATE INDEX IF NOT EXISTS idx_event_offsets_session ON event_offsets(session_id)",
            ],
        ),
        # v8: Batch C — sessions_fts for BM25 + role-based column-scoped search.
        # C-BL-02: version = 8 (B already took v7).
        # Contentless FTS5: session_id UNINDEXED (col 0, still counted by snippet/bm25),
        # title (col 1), user_messages (col 2), assistant_messages (col 3), tool_names (col 4).
        # Empirically verified column indices before committing (see _fts5_empirical.py).
        (
            8,
            "add_sessions_fts",
            [
                """CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5(
                session_id UNINDEXED,
                title,
                user_messages,
                assistant_messages,
                tool_names,
                tokenize='porter unicode61 remove_diacritics 2'
            )""",
            ],
        ),
        # v9: F15 Eval/Feedback — records thumbs up/down on search results.
        (
            9,
            "search_feedback_table",
            [
                """CREATE TABLE IF NOT EXISTS search_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT,
                result_id TEXT,
                result_kind TEXT,
                verdict INTEGER NOT NULL CHECK(verdict IN (-1,0,1)),
                comment TEXT,
                user_agent TEXT,
                created_at TEXT NOT NULL
            )""",
                "CREATE INDEX IF NOT EXISTS idx_sf_query ON search_feedback(query)",
                "CREATE INDEX IF NOT EXISTS idx_sf_created ON search_feedback(created_at)",
            ],
        ),
        (
            10,
            "phase3_schema_provenance",
            [
                "ALTER TABLE knowledge_entries ADD COLUMN task_id TEXT DEFAULT ''",
                "ALTER TABLE knowledge_entries ADD COLUMN affected_files TEXT DEFAULT '[]'",
                "ALTER TABLE knowledge_entries ADD COLUMN source_section TEXT DEFAULT ''",
                "ALTER TABLE knowledge_entries ADD COLUMN source_file TEXT DEFAULT ''",
                "ALTER TABLE knowledge_entries ADD COLUMN start_line INTEGER DEFAULT 0",
                "ALTER TABLE knowledge_entries ADD COLUMN end_line INTEGER DEFAULT 0",
                "ALTER TABLE knowledge_entries ADD COLUMN code_language TEXT DEFAULT ''",
                "ALTER TABLE knowledge_entries ADD COLUMN code_snippet TEXT DEFAULT ''",
                "CREATE INDEX IF NOT EXISTS idx_ke_task ON knowledge_entries(task_id)",
            ],
        ),
        (
            11,
            "phase5_recall_events",
            [
                """CREATE TABLE IF NOT EXISTS recall_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                event_kind TEXT NOT NULL,
                tool TEXT NOT NULL,
                surface TEXT NOT NULL,
                mode TEXT DEFAULT '',
                raw_query TEXT DEFAULT '',
                rewritten_query TEXT DEFAULT '',
                task_id TEXT DEFAULT '',
                files TEXT DEFAULT '[]',
                selected_entry_ids TEXT DEFAULT '[]',
                selected_snippet_ids TEXT DEFAULT '[]',
                opened_entry_id INTEGER,
                hit_count INTEGER DEFAULT 0,
                output_chars INTEGER DEFAULT 0,
                output_est_tokens INTEGER DEFAULT 0
            )""",
                "CREATE INDEX IF NOT EXISTS idx_recall_events_created_at ON recall_events(created_at)",
                "CREATE INDEX IF NOT EXISTS idx_recall_events_tool_surface ON recall_events(tool, surface)",
                "CREATE INDEX IF NOT EXISTS idx_recall_events_rewritten_query ON recall_events(rewritten_query)",
                "CREATE INDEX IF NOT EXISTS idx_recall_events_opened_entry_id ON recall_events(opened_entry_id)",
            ],
        ),
        (
            12,
            "stable_ids_and_sync_metadata",
            [
                """CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                doc_type TEXT NOT NULL,
                seq INTEGER DEFAULT 0,
                title TEXT NOT NULL,
                file_path TEXT NOT NULL UNIQUE,
                file_hash TEXT,
                size_bytes INTEGER DEFAULT 0,
                content_preview TEXT DEFAULT '',
                source TEXT DEFAULT 'copilot',
                indexed_at TEXT
            )""",
                """CREATE TABLE IF NOT EXISTS sections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                section_name TEXT NOT NULL,
                content TEXT NOT NULL,
                UNIQUE(document_id, section_name)
            )""",
                """CREATE TABLE IF NOT EXISTS knowledge_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                topic_key TEXT
            )""",
                """CREATE TABLE IF NOT EXISTS knowledge_relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER,
                target_id INTEGER,
                relation_type TEXT NOT NULL
            )""",
                """CREATE TABLE IF NOT EXISTS entity_relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL
            )""",
                """CREATE TABLE IF NOT EXISTS search_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT,
                result_id TEXT,
                result_kind TEXT,
                verdict INTEGER NOT NULL CHECK(verdict IN (-1,0,1)),
                comment TEXT,
                user_agent TEXT,
                created_at TEXT NOT NULL
            )""",
                "ALTER TABLE documents ADD COLUMN stable_id TEXT",
                "ALTER TABLE sections ADD COLUMN stable_id TEXT",
                "ALTER TABLE knowledge_entries ADD COLUMN stable_id TEXT",
                "ALTER TABLE knowledge_relations ADD COLUMN source_stable_id TEXT",
                "ALTER TABLE knowledge_relations ADD COLUMN target_stable_id TEXT",
                "ALTER TABLE knowledge_relations ADD COLUMN stable_id TEXT",
                "ALTER TABLE entity_relations ADD COLUMN stable_id TEXT",
                "ALTER TABLE search_feedback ADD COLUMN origin_replica_id TEXT DEFAULT 'local'",
                "ALTER TABLE search_feedback ADD COLUMN stable_id TEXT",
                "CREATE INDEX IF NOT EXISTS idx_documents_stable_id ON documents(stable_id)",
                "CREATE INDEX IF NOT EXISTS idx_sections_stable_id ON sections(stable_id)",
                "CREATE INDEX IF NOT EXISTS idx_ke_stable_id ON knowledge_entries(stable_id)",
                "CREATE INDEX IF NOT EXISTS idx_kr_source_stable ON knowledge_relations(source_stable_id)",
                "CREATE INDEX IF NOT EXISTS idx_kr_target_stable ON knowledge_relations(target_stable_id)",
                "CREATE INDEX IF NOT EXISTS idx_kr_stable_id ON knowledge_relations(stable_id)",
                "CREATE INDEX IF NOT EXISTS idx_er_stable_id ON entity_relations(stable_id)",
                "CREATE INDEX IF NOT EXISTS idx_sf_stable_id ON search_feedback(stable_id)",
                "CREATE INDEX IF NOT EXISTS idx_sf_origin_replica ON search_feedback(origin_replica_id)",
                """CREATE TABLE IF NOT EXISTS sync_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            )""",
                """CREATE TABLE IF NOT EXISTS sync_table_policies (
                table_name TEXT PRIMARY KEY,
                sync_scope TEXT NOT NULL CHECK(sync_scope IN ('canonical', 'local_only', 'upload_only')),
                stable_id_column TEXT DEFAULT ''
            )""",
            ],
        ),
        (
            13,
            "sync_foundation_tables",
            [
                """CREATE TABLE IF NOT EXISTS sync_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            )""",
                """CREATE TABLE IF NOT EXISTS sync_txns (
                txn_id TEXT PRIMARY KEY,
                replica_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'committed', 'failed')),
                created_at TEXT NOT NULL,
                committed_at TEXT DEFAULT ''
            )""",
                """CREATE TABLE IF NOT EXISTS sync_ops (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                txn_id TEXT NOT NULL,
                table_name TEXT NOT NULL,
                op_type TEXT NOT NULL CHECK(op_type IN ('insert', 'update', 'delete', 'upsert')),
                row_stable_id TEXT NOT NULL,
                row_payload TEXT NOT NULL,
                op_index INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(txn_id, op_index)
            )""",
                "CREATE INDEX IF NOT EXISTS idx_sync_ops_txn ON sync_ops(txn_id)",
                "CREATE INDEX IF NOT EXISTS idx_sync_ops_table_row ON sync_ops(table_name, row_stable_id)",
                """CREATE TABLE IF NOT EXISTS sync_cursors (
                replica_id TEXT PRIMARY KEY,
                last_txn_id TEXT DEFAULT '',
                updated_at TEXT DEFAULT (datetime('now'))
            )""",
                """CREATE TABLE IF NOT EXISTS sync_failures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                txn_id TEXT DEFAULT '',
                table_name TEXT DEFAULT '',
                row_stable_id TEXT DEFAULT '',
                error_code TEXT DEFAULT '',
                error_message TEXT DEFAULT '',
                failed_at TEXT NOT NULL,
                retry_count INTEGER DEFAULT 0
            )""",
                "CREATE INDEX IF NOT EXISTS idx_sync_failures_txn ON sync_failures(txn_id)",
            ],
        ),
        (
            14,
            "benchmark_snapshots",
            [
                """CREATE TABLE IF NOT EXISTS benchmark_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                commit_sha TEXT NOT NULL DEFAULT '',
                commit_msg TEXT DEFAULT '',
                recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
                mode TEXT NOT NULL DEFAULT 'repo',
                retro_score REAL DEFAULT 0.0,
                score_confidence TEXT DEFAULT '',
                subscores_json TEXT NOT NULL DEFAULT '{}',
                health_score REAL DEFAULT NULL,
                health_json TEXT DEFAULT NULL,
                extra_json TEXT NOT NULL DEFAULT '{}'
            )""",
                "CREATE INDEX IF NOT EXISTS idx_bsnap_commit ON benchmark_snapshots(commit_sha)",
                "CREATE INDEX IF NOT EXISTS idx_bsnap_recorded ON benchmark_snapshots(recorded_at)",
            ],
        ),
        (
            15,
            "confidence_backfill_wave3",
            [
                "ALTER TABLE knowledge_entries ADD COLUMN confidence REAL DEFAULT 1.0",
                "ALTER TABLE knowledge_entries ADD COLUMN occurrence_count INTEGER DEFAULT 1",
                # Raise confidence floor for extracted patterns to 0.5
                "UPDATE knowledge_entries SET confidence = MAX(confidence, 0.5) WHERE category = 'pattern' AND confidence < 0.5",
                # Recurrence reward: bump entries seen 2+ times (capped to avoid runaway)
                "UPDATE knowledge_entries SET confidence = MIN(1.0, confidence + 0.03 * MIN(COALESCE(occurrence_count, 1) - 1, 5)) WHERE COALESCE(occurrence_count, 1) >= 2 AND confidence <= 0.92",
            ],
        ),
        (
            16,
            "error_lifecycle_columns",
            [
                "ALTER TABLE knowledge_entries ADD COLUMN error_type TEXT DEFAULT ''",
                "ALTER TABLE knowledge_entries ADD COLUMN root_cause TEXT DEFAULT ''",
                "ALTER TABLE knowledge_entries ADD COLUMN severity TEXT DEFAULT 'medium'",
                "ALTER TABLE knowledge_entries ADD COLUMN is_resolved INTEGER DEFAULT 0",
                "ALTER TABLE knowledge_entries ADD COLUMN fix_steps TEXT DEFAULT ''",
                "ALTER TABLE knowledge_entries ADD COLUMN prevention_hook TEXT DEFAULT ''",
                "ALTER TABLE knowledge_entries ADD COLUMN recurrence_after_briefing INTEGER DEFAULT 0",
            ],
        ),
        (
            17,
            "briefing_deliveries",
            [
                """CREATE TABLE IF NOT EXISTS briefing_deliveries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    entry_id INTEGER NOT NULL,
                    delivered_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(session_id, entry_id)
                )""",
                "CREATE INDEX IF NOT EXISTS idx_bd_session ON briefing_deliveries(session_id)",
                "CREATE INDEX IF NOT EXISTS idx_bd_entry ON briefing_deliveries(entry_id)",
            ],
        ),
        (
            18,
            "entry_recall_telemetry",
            [
                # Aggregated per-entry recall stats (one row per knowledge_entries.id).
                """CREATE TABLE IF NOT EXISTS entry_recall_stats (
                    entry_id INTEGER PRIMARY KEY,
                    recall_count INTEGER NOT NULL DEFAULT 0,
                    recall_days INTEGER NOT NULL DEFAULT 0,
                    unique_queries INTEGER NOT NULL DEFAULT 0,
                    first_recalled_at TEXT,
                    last_recalled_at TEXT
                )""",
                "CREATE INDEX IF NOT EXISTS idx_ers_last_recalled ON entry_recall_stats(last_recalled_at)",
                # Dedupe log: one row per (entry_id, calendar day) — prevents double-counting same-day recalls.
                """CREATE TABLE IF NOT EXISTS entry_recall_day_log (
                    entry_id INTEGER NOT NULL,
                    day TEXT NOT NULL,
                    PRIMARY KEY (entry_id, day)
                )""",
                # Dedupe log: one row per (entry_id, query_hash) — prevents double-counting same-query recalls.
                """CREATE TABLE IF NOT EXISTS entry_recall_query_log (
                    entry_id INTEGER NOT NULL,
                    query_hash TEXT NOT NULL,
                    PRIMARY KEY (entry_id, query_hash)
                )""",
            ],
        ),
        (
            19,
            "entry_concept_tags",
            [
                # Auto-extracted concept tags per knowledge entry (local_only: never synced).
                """CREATE TABLE IF NOT EXISTS entry_concept_tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entry_id INTEGER NOT NULL,
                    tag TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'auto',
                    tagged_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(entry_id, tag)
                )""",
                "CREATE INDEX IF NOT EXISTS idx_ect_entry ON entry_concept_tags(entry_id)",
                "CREATE INDEX IF NOT EXISTS idx_ect_tag ON entry_concept_tags(tag)",
            ],
        ),
        (
            20,
            "dream_scores",
            [
                # Persisted dream-score per knowledge entry (issue #159, local_only).
                """CREATE TABLE IF NOT EXISTS entry_dream_scores (
                    entry_id INTEGER PRIMARY KEY,
                    score REAL NOT NULL DEFAULT 0.0,
                    signal_frequency REAL DEFAULT 0.0,
                    signal_relevance REAL DEFAULT 0.0,
                    signal_diversity REAL DEFAULT 0.0,
                    signal_recency REAL DEFAULT 0.0,
                    signal_consolidation REAL DEFAULT 0.0,
                    signal_conceptual REAL DEFAULT 0.0,
                    passes_gate INTEGER NOT NULL DEFAULT 0,
                    scored_at TEXT DEFAULT (datetime('now'))
                )""",
                "CREATE INDEX IF NOT EXISTS idx_eds_score ON entry_dream_scores(score DESC)",
                "CREATE INDEX IF NOT EXISTS idx_eds_passes_gate ON entry_dream_scores(passes_gate)",
            ],
        ),
        # v21: issue #88 — Valence + Intensity metadata (Hippocampus-inspired).
        # valence: reward | neutral | penalty | trauma (empty = unset/legacy).
        # intensity: 0.0 (weak) to 1.0 (strong signal), default 0.5.
        # High-intensity penalty/trauma entries rank higher in briefing.
        (
            21,
            "valence_intensity",
            [
                "ALTER TABLE knowledge_entries ADD COLUMN valence TEXT DEFAULT ''",
                "ALTER TABLE knowledge_entries ADD COLUMN intensity REAL DEFAULT 0.5",
                "CREATE INDEX IF NOT EXISTS idx_ke_intensity ON knowledge_entries(intensity DESC)",
            ],
        ),
        # v22: issue #121 — Priority classification (P0-P3).
        # P0 = critical (highest priority), P1 = high, P2 = normal (default), P3 = low.
        # briefing.py surfaces higher-priority entries first within each category.
        (
            22,
            "priority",
            [
                "ALTER TABLE knowledge_entries ADD COLUMN priority TEXT DEFAULT 'P2'",
                "CREATE INDEX IF NOT EXISTS idx_ke_priority ON knowledge_entries(priority)",
            ],
        ),
        # v23: issue #83 — Per-file anatomy index (local_only; never synced).
        # Keyed by (repo_root, file_path); file_mtime enables incremental updates.
        (
            23,
            "file_annotations",
            [
                """CREATE TABLE IF NOT EXISTS file_annotations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    repo_root TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    est_tokens INTEGER NOT NULL DEFAULT 0,
                    file_mtime REAL NOT NULL DEFAULT 0.0,
                    updated_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(repo_root, file_path)
                )""",
                "CREATE INDEX IF NOT EXISTS idx_fa_repo_root ON file_annotations(repo_root)",
            ],
        ),
        # v24: issue #126 — Explicit improvement signal tracking.
        # Records user-reported missed_match / wrong_skill / outdated_skill signals
        # linked to session IDs, consumable by skill-suggest for candidate boosting.
        (
            24,
            "improvement_signals",
            [
                """CREATE TABLE IF NOT EXISTS improvement_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL DEFAULT '',
                    query TEXT NOT NULL DEFAULT '',
                    signal_type TEXT NOT NULL CHECK(signal_type IN ('missed_match', 'wrong_skill', 'outdated_skill')),
                    mentioned_skill TEXT NOT NULL DEFAULT '',
                    consumed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )""",
                "CREATE INDEX IF NOT EXISTS idx_is_consumed ON improvement_signals(consumed)",
                "CREATE INDEX IF NOT EXISTS idx_is_signal_type ON improvement_signals(signal_type)",
                "CREATE INDEX IF NOT EXISTS idx_is_created ON improvement_signals(created_at)",
                "CREATE INDEX IF NOT EXISTS idx_is_mentioned_skill ON improvement_signals(mentioned_skill)",
            ],
        ),
        # v25: issue #373 — Add porter tokenizer to ke_fts for better stem matching.
        # ke_fts was previously using 'unicode61 remove_diacritics 2'.
        # Rebuild happens in the post-migration FTS check below using _rebuild_ke_fts_batched.
        # This migration serves as the version marker so the rebuild only runs once.
        (
            25,
            "ke_fts_porter_tokenizer",
            [
                # The actual rebuild is done by _rebuild_ke_fts_batched below
                # after all version migrations complete.  This entry pins the
                # schema version so idempotency is preserved (#357, #358, #373).
                "SELECT 1",  # no-op placeholder — rebuild done post-migration
            ],
        ),
        # v26: issue #372 — Project-scoped knowledge search.
        # Adds project_id column to knowledge_entries and a project_registry table
        # so entries can be filtered by project/repo root without relying on wing/room.
        (
            26,
            "project_scoped_knowledge",
            [
                "ALTER TABLE knowledge_entries ADD COLUMN project_id TEXT DEFAULT ''",
                "CREATE INDEX IF NOT EXISTS idx_ke_project_id ON knowledge_entries(project_id)",
                # Project registry: named projects with repo roots for scoped querying (#372)
                """CREATE TABLE IF NOT EXISTS project_registry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL DEFAULT '',
                    repo_root TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now'))
                )""",
                "CREATE INDEX IF NOT EXISTS idx_pr_project_id ON project_registry(project_id)",
                "CREATE INDEX IF NOT EXISTS idx_pr_repo_root ON project_registry(repo_root)",
            ],
        ),
        # v27: issue #351 — agent_id for multi-agent memory routing.
        # Allows entries to be tagged with the writing agent's ID and
        # filtered per-agent when building context packs or briefings.
        (
            27,
            "agent_id",
            [
                "ALTER TABLE knowledge_entries ADD COLUMN agent_id TEXT DEFAULT ''",
                "CREATE INDEX IF NOT EXISTS idx_ke_agent_id ON knowledge_entries(agent_id)",
            ],
        ),
        # v28: issue #387 — Soft-delete for stale/low-value entries.
        # deleted_at IS NULL → active; non-NULL ISO timestamp → soft-deleted.
        # All read paths filter WHERE deleted_at IS NULL (or column absent).
        (
            28,
            "soft_delete",
            [
                "ALTER TABLE knowledge_entries ADD COLUMN deleted_at TEXT DEFAULT NULL",
                "CREATE INDEX IF NOT EXISTS idx_ke_deleted_at ON knowledge_entries(deleted_at)",
            ],
        ),
        # v29: issue #402 — Epistemic humility fields.
        # certainty: human-readable qualifier ("high", "medium", "low", "uncertain").
        # caveats: freeform exceptions/conditions where the entry may not apply.
        (
            29,
            "epistemic_humility",
            [
                "ALTER TABLE knowledge_entries ADD COLUMN certainty TEXT DEFAULT ''",
                "ALTER TABLE knowledge_entries ADD COLUMN caveats TEXT DEFAULT ''",
            ],
        ),
        # v30: issues #394/#395 — Episode batch capture and session compile tables.
        # episode_batches: periodic tool-event episode summaries (WBS-069/Issue #394).
        #   - episode_hash UNIQUE: deduplication contract; same episode never stored twice.
        #   - session_id index: fast per-session queries for briefing surface.
        # compile_cursors: hash-gated session compile checkpoints (WBS-070/Issue #395).
        #   - session_id PRIMARY KEY: one cursor row per session.
        #   - source_hash: content hash of source entries; skip recompile when unchanged.
        (
            30,
            "episode_batch_compile",
            [
                """CREATE TABLE IF NOT EXISTS episode_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL DEFAULT '',
                    episode_hash TEXT NOT NULL UNIQUE,
                    tool_fingerprint TEXT NOT NULL DEFAULT '',
                    threshold_count INTEGER NOT NULL DEFAULT 10,
                    summary TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )""",
                "CREATE INDEX IF NOT EXISTS idx_ep_session ON episode_batches(session_id)",
                "CREATE INDEX IF NOT EXISTS idx_ep_created ON episode_batches(created_at)",
                """CREATE TABLE IF NOT EXISTS compile_cursors (
                    session_id TEXT PRIMARY KEY,
                    source_hash TEXT NOT NULL DEFAULT '',
                    compiled_at TEXT NOT NULL DEFAULT (datetime('now'))
                )""",
            ],
        ),
        # v31: issue #456 — Composite indexes for hot query paths + sync table created_at indexes.
        # knowledge_entries composite indexes:
        #   - (category, wing, room, confidence): covers search_by_wing_room() filtering
        #     in fts.rs and query-session.py category+wing+room lookups.
        #   - (session_id, category): covers per-session category queries.
        #   - (source, task_id): covers provenance lookups filtered by source and task.
        # sync table timestamp indexes support the pruning cron task (cron-tasks.py
        # sync_pruning template) without requiring full table scans.
        # The ALTER TABLE is idempotent: the migration runner swallows "duplicate column"
        # errors so the ADD COLUMN is safe to run on DBs that already have confidence.
        # CREATE TABLE IF NOT EXISTS guards ensure sync tables exist before their indexes
        # are created; _seed_sync_table_policies re-runs these with the same DDL after
        # migrations so there is no double-ownership concern.
        (
            31,
            "composite_indexes_sync_timestamps",
            [
                # Ensure columns exist before creating indexes that cover them.
                # Legacy DBs created manually at a high version number (e.g. v22)
                # may never have had the earlier ALTER TABLE migrations applied.
                # These ALTERs are idempotent: "duplicate column" errors are swallowed.
                "ALTER TABLE knowledge_entries ADD COLUMN confidence REAL DEFAULT 1.0",
                "ALTER TABLE knowledge_entries ADD COLUMN session_id TEXT DEFAULT ''",
                "ALTER TABLE knowledge_entries ADD COLUMN source TEXT DEFAULT 'copilot'",
                "ALTER TABLE knowledge_entries ADD COLUMN task_id TEXT DEFAULT ''",
                "CREATE INDEX IF NOT EXISTS idx_ke_cat_wing_room_conf ON knowledge_entries(category, wing, room, confidence)",
                "CREATE INDEX IF NOT EXISTS idx_ke_session_cat ON knowledge_entries(session_id, category)",
                "CREATE INDEX IF NOT EXISTS idx_ke_source_task ON knowledge_entries(source, task_id)",
                # Ensure sync tables exist before adding indexes; legacy DBs (< v13) may
                # not have them yet at this point in the migration sequence.
                """CREATE TABLE IF NOT EXISTS sync_txns (
                    txn_id TEXT PRIMARY KEY,
                    replica_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending', 'committed', 'failed')),
                    created_at TEXT NOT NULL,
                    committed_at TEXT DEFAULT ''
                )""",
                """CREATE TABLE IF NOT EXISTS sync_ops (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    txn_id TEXT NOT NULL,
                    table_name TEXT NOT NULL,
                    op_type TEXT NOT NULL CHECK(op_type IN ('insert', 'update', 'delete', 'upsert')),
                    row_stable_id TEXT NOT NULL,
                    row_payload TEXT NOT NULL,
                    op_index INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(txn_id, op_index)
                )""",
                """CREATE TABLE IF NOT EXISTS sync_failures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    txn_id TEXT DEFAULT '',
                    table_name TEXT DEFAULT '',
                    row_stable_id TEXT DEFAULT '',
                    error_code TEXT DEFAULT '',
                    error_message TEXT DEFAULT '',
                    failed_at TEXT NOT NULL,
                    retry_count INTEGER DEFAULT 0
                )""",
                "CREATE INDEX IF NOT EXISTS idx_sync_txns_created ON sync_txns(created_at)",
                "CREATE INDEX IF NOT EXISTS idx_sync_ops_created ON sync_ops(created_at)",
                "CREATE INDEX IF NOT EXISTS idx_sync_failures_failed_at ON sync_failures(failed_at)",
            ],
        ),
        (
            32,
            "knowledge_relations_supersedes",
            [
                # Add session_id to knowledge_relations for SUPERSEDES tracking.
                # Existing table uses source_id/target_id INTEGER; we add session_id
                # and an index for fast superseded-entry filtering in briefing.
                "ALTER TABLE knowledge_relations ADD COLUMN session_id TEXT DEFAULT ''",
                "CREATE INDEX IF NOT EXISTS idx_kr_relation_type ON knowledge_relations(relation_type)",
                "CREATE INDEX IF NOT EXISTS idx_kr_target_supersedes ON knowledge_relations(target_id, relation_type)",
            ],
        ),
        # v33: issue #692 — Per-session cost estimates + weekly trend chart.
        # cost_usd_est: estimated USD cost from token counts × per-model rates.
        # total_input_tokens / total_output_tokens: aggregated across all models
        # in the session.shutdown event from events.jsonl (Copilot sessions only).
        (
            33,
            "session_cost_columns",
            [
                "ALTER TABLE sessions ADD COLUMN cost_usd_est REAL",
                "ALTER TABLE sessions ADD COLUMN total_input_tokens INTEGER",
                "ALTER TABLE sessions ADD COLUMN total_output_tokens INTEGER",
            ],
        ),
        # v34: issues #715/#716 — User-defined session labels for quick tagging.
        # label: freeform short text tag (e.g. "auth-refactor", "bug-fix-week12").
        # Empty string is the default (no label); index enables label-based listing.
        (
            34,
            "session_label",
            [
                "ALTER TABLE sessions ADD COLUMN label TEXT DEFAULT ''",
                "CREATE INDEX IF NOT EXISTS idx_sessions_label ON sessions(label)",
            ],
        ),
        # v35: issue #740 — Lexical source-code search via ripgrep + SQLite FTS5.
        # code_index: mtime-tracked symbol/chunk table; code_fts: FTS5 search index.
        (
            35,
            "code_index",
            [
                """CREATE TABLE IF NOT EXISTS code_index (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL DEFAULT '',
                    file_path TEXT NOT NULL,
                    language TEXT NOT NULL DEFAULT '',
                    symbol_kind TEXT NOT NULL DEFAULT '',
                    symbol_name TEXT NOT NULL DEFAULT '',
                    start_line INTEGER NOT NULL DEFAULT 0,
                    end_line INTEGER NOT NULL DEFAULT 0,
                    content_snippet TEXT NOT NULL DEFAULT '',
                    file_mtime REAL NOT NULL DEFAULT 0.0,
                    indexed_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(project_id, file_path, start_line, symbol_name)
                )""",
                "CREATE INDEX IF NOT EXISTS idx_ci_project ON code_index(project_id)",
                "CREATE INDEX IF NOT EXISTS idx_ci_language ON code_index(language)",
                "CREATE INDEX IF NOT EXISTS idx_ci_symbol ON code_index(symbol_name)",
                "CREATE INDEX IF NOT EXISTS idx_ci_file ON code_index(file_path)",
                "CREATE INDEX IF NOT EXISTS idx_ci_mtime ON code_index(file_mtime)",
                """CREATE VIRTUAL TABLE IF NOT EXISTS code_fts USING fts5(
                    symbol_name,
                    content_snippet,
                    file_path UNINDEXED,
                    language UNINDEXED,
                    project_id UNINDEXED,
                    tokenize='porter unicode61 remove_diacritics 2'
                )""",
            ],
        ),
    ]
    applied = 0
    for ver, name, stmts in MIGRATIONS:
        if ver <= current:
            continue
        try:
            for sql in stmts:
                try:
                    db.execute(sql)
                except sqlite3.OperationalError as e:
                    if "duplicate" in str(e).lower() or "already exists" in str(e).lower():
                        pass
                    else:
                        raise
            db.execute("INSERT OR IGNORE INTO schema_version (version, name) VALUES (?, ?)", (ver, name))
            db.commit()
            applied += 1
            print(f"  [migrate] v{ver}: {name}")
        except Exception as e:
            db.rollback()
            print(f"  [migrate] v{ver} {name}: {e}", file=sys.stderr)
            print(
                "  [migrate] Recovery hint: restore a backup or fix the schema error before retrying. "
                "Run `python migrate.py DB_PATH --backup-only --backup-path BACKUP_PATH` before manual repair.",
                file=sys.stderr,
            )
            db.close()
            raise SystemExit(1) from None
    try:
        repaired_priority, renamed_priority = _repair_legacy_priority_collision(db)
        if repaired_priority:
            print("  [migrate] collision-repair: priority column added to knowledge_entries (legacy v22 overlap)")
        if renamed_priority:
            print("  [migrate] collision-repair: schema_version v22 renamed to 'priority'")
    except Exception as e:
        print(f"  [migrate] collision-repair: {e}", file=sys.stderr)
    try:
        _backfill_stable_ids(db)
        _seed_sync_table_policies(db)
        _enforce_stable_id_uniqueness(db)
        db.commit()
    except Exception as e:
        print(f"  [migrate] stable-id backfill: {e}", file=sys.stderr)
    # ── FTS5 schema check and batched rebuild (#357, #358, #373) ─────────────
    # Target DDL: porter tokenizer + all required columns.
    _KE_FTS_TARGET_DDL = (
        "CREATE VIRTUAL TABLE ke_fts USING fts5("
        "title, content, tags, category, wing, room, facts, error_type, root_cause, "
        "tokenize='porter unicode61 remove_diacritics 2')"
    )
    try:
        if _ke_fts_needs_rebuild(db):
            print("  [migrate] Rebuilding ke_fts with porter tokenizer + all columns (#357/#373)...")
            _rebuild_ke_fts_batched(db, _KE_FTS_TARGET_DDL)
            _set_cached_ke_fts_version(db, _KE_FTS_SCHEMA_VERSION)
            db.commit()
            print("  [migrate] ke_fts rebuilt successfully")
        else:
            # Ensure cache key is set even when no rebuild needed (#358)
            _set_cached_ke_fts_version(db, _KE_FTS_SCHEMA_VERSION)
            db.commit()
    except Exception as e:
        print(f"  [migrate] ke_fts rebuild: {e}", file=sys.stderr)
    # ── Post-migration WAL checkpoint (#392) ─────────────────────────────────
    try:
        schedule_wal_checkpoint(db, threshold_pages=500)
    except Exception:
        pass
    if applied == 0:
        print(f"  [migrate] Schema up to date (v{current})")
    else:
        print(f"  [migrate] Applied {applied} migration(s)")
    db.close()
