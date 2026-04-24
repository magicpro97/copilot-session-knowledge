#!/usr/bin/env python3
"""Versioned DB migration for session-knowledge tools."""
import sqlite3, sys, os

if len(sys.argv) < 2:
    sys.argv.append(os.path.expanduser("~/.copilot/session-state/knowledge.db"))
db = sqlite3.connect(sys.argv[1])
db.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, migrated_at TEXT DEFAULT (datetime('now')))")
# Add name column if missing (compat with old schema)
try:
    db.execute("ALTER TABLE schema_version ADD COLUMN name TEXT DEFAULT ''")
except sqlite3.OperationalError:
    pass
current = db.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0
MIGRATIONS = [
    (2, "add_wing_room", [
        "ALTER TABLE knowledge_entries ADD COLUMN wing TEXT DEFAULT ''",
        "ALTER TABLE knowledge_entries ADD COLUMN room TEXT DEFAULT ''",
    ]),
    (3, "entity_relations", [
        "CREATE TABLE IF NOT EXISTS entity_relations (id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT NOT NULL, predicate TEXT NOT NULL, object TEXT NOT NULL, noted_at TEXT DEFAULT (datetime('now')), session_id TEXT DEFAULT '', UNIQUE(subject, predicate, object))",
        "CREATE INDEX IF NOT EXISTS idx_er_subject ON entity_relations(subject)",
        "CREATE INDEX IF NOT EXISTS idx_er_object ON entity_relations(object)",
    ]),
    (4, "wakeup_config", [
        "CREATE TABLE IF NOT EXISTS wakeup_config (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT DEFAULT (datetime('now')))",
    ]),
    (5, "add_facts_column", [
        "ALTER TABLE knowledge_entries ADD COLUMN facts TEXT DEFAULT '[]'",
    ]),
    (6, "add_est_tokens_column", [
        "ALTER TABLE knowledge_entries ADD COLUMN est_tokens INTEGER DEFAULT 0",
        "UPDATE knowledge_entries SET est_tokens = LENGTH(COALESCE(title,'') || ' ' || COALESCE(content,'')) / 4 WHERE est_tokens = 0",
    ]),
    # v7: Batch B — two-phase indexing.
    # B-BL-07: CREATE TABLE IF NOT EXISTS sessions first so ALTERs don't fail on fresh DB.
    # B-BL-02: event_offsets.event_id is INTEGER NOT NULL (not TEXT).
    # B-BL-05: event_offsets has file_mtime REAL column.
    (7, "two_phase_indexing", [
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
    ]),
]
applied = 0
for ver, name, stmts in MIGRATIONS:
    if ver <= current: continue
    try:
        for sql in stmts:
            try: db.execute(sql)
            except sqlite3.OperationalError as e:
                if "duplicate" in str(e).lower() or "already exists" in str(e).lower(): pass
                else: raise
        db.execute("INSERT OR IGNORE INTO schema_version (version, name) VALUES (?, ?)", (ver, name))
        db.commit(); applied += 1
        print(f"  [migrate] v{ver}: {name}")
    except Exception as e:
        print(f"  [migrate] v{ver} {name}: {e}", file=sys.stderr)
try:
    fts_sql = db.execute("SELECT sql FROM sqlite_master WHERE name='ke_fts'").fetchone()
    needs_rebuild = False
    if fts_sql:
        fts_def = fts_sql[0] or ''
        if 'wing' not in fts_def or 'facts' not in fts_def:
            needs_rebuild = True
    if needs_rebuild:
        print("  [migrate] Rebuilding FTS5 (adding facts column)...")
        # P0-9: use BEGIN EXCLUSIVE so the DROP→RENAME is atomic;
        # prevents FTS permanent loss if watch-sessions holds a read transaction.
        db.execute("BEGIN EXCLUSIVE")
        try:
            db.execute("DROP TABLE IF EXISTS ke_fts_new")
            db.execute("CREATE VIRTUAL TABLE ke_fts_new USING fts5(title, content, tags, category, wing, room, facts, tokenize='unicode61 remove_diacritics 2')")
            db.execute("INSERT INTO ke_fts_new(rowid, title, content, tags, category, wing, room, facts) SELECT id, title, content, tags, category, COALESCE(wing,''), COALESCE(room,''), COALESCE(facts,'[]') FROM knowledge_entries")
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
if applied == 0: print(f"  [migrate] Schema up to date (v{current})")
else: print(f"  [migrate] Applied {applied} migration(s)")
db.close()
