-- Seed data for browse_communities_api_test.rs
-- Produces exactly 2 communities: c-1 (3 entries) and c-4 (2 entries)

PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL,
    name    TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS knowledge_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category    TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL DEFAULT '',
    content     TEXT NOT NULL DEFAULT '',
    tags        TEXT NOT NULL DEFAULT '',
    wing        TEXT,
    room        TEXT,
    confidence  REAL NOT NULL DEFAULT 0.5,
    deleted_at  INTEGER
);

CREATE TABLE IF NOT EXISTS knowledge_relations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       INTEGER,
    target_id       INTEGER,
    relation_type   TEXT NOT NULL,
    confidence      REAL DEFAULT 0.8
);

INSERT INTO schema_version (version, name) VALUES (1, 'test');

-- Community 1 (c-1): entries 1, 2, 3 — all backend wing
INSERT INTO knowledge_entries (id, category, title, content, tags, wing, room)
VALUES
    (1, 'pattern',  'Auth Pattern',     'auth content',     '', 'backend',  'auth'),
    (2, 'mistake',  'Login Fix',        'login content',    '', 'backend',  'auth'),
    (3, 'decision', 'Token Decision',   'token content',    '', 'backend',  'auth');

-- Community 2 (c-4): entries 4, 5 — frontend wing
INSERT INTO knowledge_entries (id, category, title, content, tags, wing, room)
VALUES
    (4, 'pattern',  'UI Component',  'ui content',  '', 'frontend', 'ui'),
    (5, 'mistake',  'CSS Mistake',   'css content', '', 'frontend', 'ui');

-- Relations for community 1: triangle (1-2, 2-3, 1-3)
INSERT INTO knowledge_relations (id, source_id, target_id, relation_type)
VALUES
    (1, 1, 2, 'related'),
    (2, 2, 3, 'related'),
    (3, 1, 3, 'similar');

-- Relations for community 2: single edge (4-5)
INSERT INTO knowledge_relations (id, source_id, target_id, relation_type)
VALUES
    (4, 4, 5, 'related');
