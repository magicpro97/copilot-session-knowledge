#!/usr/bin/env python3
"""Versioned DB migration for session-knowledge tools."""
import sqlite3, sys, os

if len(sys.argv) < 2:
    sys.argv.append(os.path.expanduser("~/.copilot/session-state/knowledge.db"))
db = sqlite3.connect(sys.argv[1])
db.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT DEFAULT (datetime('now')))" )
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
        db.execute("INSERT INTO schema_version (version, name) VALUES (?, ?)", (ver, name))
        db.commit(); applied += 1
        print(f"  [migrate] v{ver}: {name}")
    except Exception as e:
        print(f"  [migrate] v{ver} {name}: {e}", file=sys.stderr)
try:
    fts_sql = db.execute("SELECT sql FROM sqlite_master WHERE name='ke_fts'").fetchone()
    if fts_sql and 'wing' not in (fts_sql[0] or ''):
        print("  [migrate] Rebuilding FTS5...")
        db.execute("DROP TABLE IF EXISTS ke_fts")
        db.execute("CREATE VIRTUAL TABLE ke_fts USING fts5(title, content, tags, category, wing, room, content='knowledge_entries', content_rowid='id')")
        db.execute("INSERT INTO ke_fts(rowid, title, content, tags, category, wing, room) SELECT id, title, content, tags, category, COALESCE(wing,''), COALESCE(room,'') FROM knowledge_entries")
        db.commit(); print("  [migrate] FTS5 rebuilt")
except Exception as e:
    print(f"  [migrate] FTS5: {e}", file=sys.stderr)
if applied == 0: print(f"  [migrate] Schema up to date (v{current})")
else: print(f"  [migrate] Applied {applied} migration(s)")
db.close()
