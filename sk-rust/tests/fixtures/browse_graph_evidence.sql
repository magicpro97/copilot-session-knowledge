-- Seed data for browse_graph_evidence_api_test.rs
-- Fixture: 5 knowledge_entries across 2 wings (backend, frontend),
--          3 rooms (auth, ui, devops), 5 categories;
--          knowledge_relations with (id, source_id, target_id, relation_type, confidence)
--          including RESOLVED_BY 0.88, TAG_OVERLAP 0.55, SAME_SESSION 0.70.
--
-- Golden JSON was computed by running Python _build_evidence_graph_data against
-- this fixture with no filters and limit=500.  Regenerate with:
--   python -c "
--   import sqlite3, json, sys
--   sys.path.insert(0, '.')
--   conn = sqlite3.connect(':memory:')
--   conn.executescript(open('tests/fixtures/browse_graph_evidence.sql').read())
--   from browse.routes.graph import _build_evidence_graph_data
--   print(json.dumps(_build_evidence_graph_data(conn,'','','','',500), indent=2))
--   "

PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL,
    name    TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS knowledge_entries (
    id          INTEGER PRIMARY KEY,
    category    TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL DEFAULT '',
    content     TEXT NOT NULL DEFAULT '',
    tags        TEXT NOT NULL DEFAULT '',
    wing        TEXT,
    room        TEXT,
    confidence  REAL NOT NULL DEFAULT 0.5,
    deleted_at  INTEGER
);

-- knowledge_relations uses source_id/target_id (canonical schema)
CREATE TABLE IF NOT EXISTS knowledge_relations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id     INTEGER NOT NULL,
    target_id     INTEGER NOT NULL,
    relation_type TEXT NOT NULL DEFAULT '',
    confidence    REAL
);

INSERT INTO schema_version (version, name) VALUES (1, 'test');

-- 5 entries: 2 wings (backend, frontend), 3 rooms (auth, ui, devops), 5 categories
INSERT INTO knowledge_entries (id, category, title, content, tags, wing, room)
VALUES
    (100, 'mistake',   'Auth Bug',      '', '', 'backend',  'auth'),
    (101, 'pattern',   'Auth Pattern',  '', '', 'backend',  'auth'),
    (102, 'decision',  'UI Decision',   '', '', 'frontend', 'ui'),
    (103, 'discovery', 'CSS Discovery', '', '', 'frontend', 'ui'),
    (104, 'tool',      'Lint Tool',     '', '', 'backend',  'devops');

-- 3 relations covering the 3 required relation_types:
--   id=1: 100→101 RESOLVED_BY 0.88  (backend→backend, same wing)
--   id=2: 101→102 TAG_OVERLAP  0.55  (backend→frontend, cross-wing)
--   id=3: 100→103 SAME_SESSION 0.70  (backend→frontend, cross-wing)
INSERT INTO knowledge_relations (id, source_id, target_id, relation_type, confidence)
VALUES
    (1, 100, 101, 'RESOLVED_BY',  0.88),
    (2, 101, 102, 'TAG_OVERLAP',  0.55),
    (3, 100, 103, 'SAME_SESSION', 0.70);
