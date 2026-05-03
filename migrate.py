#!/usr/bin/env python3
"""Versioned DB migration for session-knowledge tools."""

import hashlib
import os
import re
import sqlite3
import sys

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")


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
        ("knowledge_fts", "local_only", ""),
        ("ke_fts", "local_only", ""),
        ("sessions_fts", "local_only", ""),
        ("event_offsets", "local_only", ""),
        ("embeddings", "local_only", ""),
        ("embedding_meta", "local_only", ""),
        ("tfidf_model", "local_only", ""),
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


def _backfill_stable_ids(db: sqlite3.Connection):
    has_table = lambda t: (
        db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (t,),
        ).fetchone()
        is not None
    )

    if has_table("documents"):
        for row in db.execute("""
            SELECT id, session_id, doc_type, seq, title, COALESCE(stable_id, '')
            FROM documents
        """).fetchall():
            did, session_id, doc_type, seq, title, existing = row
            stable = _stable_sha256("document", session_id, doc_type, int(seq or 0), _normalize_title(title))
            if existing != stable:
                db.execute("UPDATE documents SET stable_id = ? WHERE id = ?", (stable, did))

    if has_table("sections") and has_table("documents"):
        for row in db.execute("""
            SELECT s.id, d.stable_id, s.section_name, COALESCE(s.stable_id, '')
            FROM sections s
            JOIN documents d ON s.document_id = d.id
            WHERE COALESCE(d.stable_id, '') != ''
        """).fetchall():
            sid, document_stable_id, section_name, existing = row
            stable = _stable_sha256("section", document_stable_id, section_name or "")
            if existing != stable:
                db.execute("UPDATE sections SET stable_id = ? WHERE id = ?", (stable, sid))

    if has_table("knowledge_entries"):
        for row in db.execute("""
            SELECT id, session_id, category, title, COALESCE(topic_key, ''), COALESCE(stable_id, '')
            FROM knowledge_entries
        """).fetchall():
            kid, session_id, category, title, topic_key, existing = row
            stable = _stable_sha256("knowledge", session_id, category, title or "", topic_key)
            if existing != stable:
                db.execute("UPDATE knowledge_entries SET stable_id = ? WHERE id = ?", (stable, kid))

    if has_table("knowledge_relations") and has_table("knowledge_entries"):
        for row in db.execute("""
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
        """).fetchall():
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
        for row in db.execute("""
            SELECT id, subject, predicate, object, COALESCE(stable_id, '')
            FROM entity_relations
        """).fetchall():
            er_id, subject, predicate, obj, existing = row
            stable = _stable_sha256("entity_relation", subject or "", predicate or "", obj or "")
            if existing != stable:
                db.execute("UPDATE entity_relations SET stable_id = ? WHERE id = ?", (stable, er_id))

    if has_table("search_feedback"):
        local_replica_id = _get_local_replica_id(db)
        for row in db.execute("""
            SELECT id, created_at, result_kind, result_id, verdict, query,
                   COALESCE(origin_replica_id, ''), COALESCE(stable_id, '')
            FROM search_feedback
        """).fetchall():
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


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.argv.append(os.path.expanduser("~/.copilot/session-state/knowledge.db"))
    db = sqlite3.connect(sys.argv[1])
    db.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, migrated_at TEXT DEFAULT (datetime('now')))"
    )
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
                # Raise confidence floor for extracted patterns to 0.5
                "UPDATE knowledge_entries SET confidence = MAX(confidence, 0.5) WHERE category = 'pattern' AND confidence < 0.5",
                # Recurrence reward: bump entries seen 2+ times (capped to avoid runaway)
                "UPDATE knowledge_entries SET confidence = MIN(1.0, confidence + 0.03 * MIN(COALESCE(occurrence_count, 1) - 1, 5)) WHERE COALESCE(occurrence_count, 1) >= 2 AND confidence <= 0.92",
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
            print(f"  [migrate] v{ver} {name}: {e}", file=sys.stderr)
    try:
        _backfill_stable_ids(db)
        _seed_sync_table_policies(db)
        _enforce_stable_id_uniqueness(db)
        db.commit()
    except Exception as e:
        print(f"  [migrate] stable-id backfill: {e}", file=sys.stderr)
    try:
        fts_sql = db.execute("SELECT sql FROM sqlite_master WHERE name='ke_fts'").fetchone()
        needs_rebuild = False
        if fts_sql:
            fts_def = fts_sql[0] or ""
            if "wing" not in fts_def or "facts" not in fts_def:
                needs_rebuild = True
        if needs_rebuild:
            print("  [migrate] Rebuilding FTS5 (adding facts column)...")
            # P0-9: use BEGIN EXCLUSIVE so the DROP→RENAME is atomic;
            # prevents FTS permanent loss if watch-sessions holds a read transaction.
            db.execute("BEGIN EXCLUSIVE")
            try:
                db.execute("DROP TABLE IF EXISTS ke_fts_new")
                db.execute(
                    "CREATE VIRTUAL TABLE ke_fts_new USING fts5(title, content, tags, category, wing, room, facts, tokenize='unicode61 remove_diacritics 2')"
                )
                db.execute(
                    "INSERT INTO ke_fts_new(rowid, title, content, tags, category, wing, room, facts) SELECT id, title, content, tags, category, COALESCE(wing,''), COALESCE(room,''), COALESCE(facts,'[]') FROM knowledge_entries"
                )
                db.execute("DROP TABLE IF EXISTS ke_fts")
                db.execute("ALTER TABLE ke_fts_new RENAME TO ke_fts")
                db.execute("COMMIT")
                print("  [migrate] FTS5 rebuilt with facts column")
            except Exception as e:
                db.execute("ROLLBACK")
                db.execute("DROP TABLE IF EXISTS ke_fts_new")
                print(f"  [migrate] FTS5 rebuild failed: {e}", file=sys.stderr)
                raise
    except Exception as e:
        print(f"  [migrate] FTS5: {e}", file=sys.stderr)
    if applied == 0:
        print(f"  [migrate] Schema up to date (v{current})")
    else:
        print(f"  [migrate] Applied {applied} migration(s)")
    db.close()
