#!/usr/bin/env python3
"""
tests/test_concept_tags.py — Focused tests for concept tag extraction, persistence,
batch tagging, and health coverage reporting.

Uses synthetic in-memory SQLite DBs; never touches the real knowledge.db.

Run:
    python tests/test_concept_tags.py
"""

import importlib.util
import os
import sqlite3
import sys
import time
from pathlib import Path

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TOOLS_DIR = Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------
_PASS = 0
_FAIL = 0
_ERRORS: list = []


def test(name: str, condition: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if condition:
        _PASS += 1
        print(f"  ✅ {name}")
    else:
        _FAIL += 1
        msg = f"  ❌ {name}" + (f" — {detail}" if detail else "")
        _ERRORS.append(msg)
        print(msg)


def section(title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))


# ---------------------------------------------------------------------------
# Load modules under test
# ---------------------------------------------------------------------------

def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_ek = _load_module("extract_knowledge_ct", TOOLS_DIR / "extract-knowledge.py")
_learn = _load_module("learn_ct", TOOLS_DIR / "learn.py")
_te = _load_module("tag_entries_ct", TOOLS_DIR / "tag-entries.py")
_kh = _load_module("knowledge_health_ct", TOOLS_DIR / "knowledge-health.py")

# ---------------------------------------------------------------------------
# Shared in-memory DB factory
# ---------------------------------------------------------------------------
_DB_COUNTER = [0]


def _new_uri() -> str:
    _DB_COUNTER[0] += 1
    return f"file:ct_test_{_DB_COUNTER[0]}?mode=memory&cache=shared"


_KE_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    category TEXT,
    title TEXT,
    content TEXT,
    tags TEXT DEFAULT '',
    confidence REAL DEFAULT 0.5,
    occurrence_count INTEGER DEFAULT 1,
    first_seen TEXT,
    last_seen TEXT,
    wing TEXT DEFAULT '',
    room TEXT DEFAULT '',
    facts TEXT DEFAULT '[]',
    est_tokens INTEGER DEFAULT 0,
    task_id TEXT DEFAULT '',
    affected_files TEXT DEFAULT '[]',
    stable_id TEXT
)
"""

_ECT_SCHEMA = """
CREATE TABLE IF NOT EXISTS entry_concept_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL,
    tag TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'auto',
    tagged_at TEXT DEFAULT (datetime('now')),
    UNIQUE(entry_id, tag)
)
"""

_STP_SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_table_policies (
    table_name TEXT PRIMARY KEY,
    sync_scope TEXT NOT NULL CHECK(sync_scope IN ('canonical', 'local_only', 'upload_only')),
    stable_id_column TEXT DEFAULT ''
)
"""


def _make_db(uri: str) -> sqlite3.Connection:
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    db.execute(_KE_SCHEMA)
    db.execute(_ECT_SCHEMA)
    db.execute("CREATE INDEX IF NOT EXISTS idx_ect_entry ON entry_concept_tags(entry_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_ect_tag ON entry_concept_tags(tag)")
    db.execute(_STP_SCHEMA)
    db.commit()
    return db


# ===========================================================================
# Section 1: extract_concept_tags — extraction logic
# ===========================================================================

section("extract_concept_tags — extraction logic (extract-knowledge.py)")

tags = _ek.extract_concept_tags("SQLite database migration error in WAL mode")
test("returns a list", isinstance(tags, list))
test("returns ≤5 tags by default", len(tags) <= 5)
test("tags are lowercase strings", all(isinstance(t, str) and t == t.lower() for t in tags))
test("known keyword extracted (sqlite)", "sqlite" in tags)
test("stopword 'in' not present", "in" not in tags)
test("stopword 'the' not present", "the" not in tags)

tags_k3 = _ek.extract_concept_tags("Python async await coroutine event loop thread", top_k=3)
test("top_k=3 returns at most 3 tags", len(tags_k3) <= 3)

empty_tags = _ek.extract_concept_tags("")
test("empty text returns empty list", empty_tags == [])

short_tags = _ek.extract_concept_tags("a an the")
test("all-stopword text returns empty list", short_tags == [])

repeated = _ek.extract_concept_tags("migration migration migration rollback rollback schema")
test("highest-freq term appears first", repeated and repeated[0] == "migration")

section("extract_concept_tags — extraction logic (learn.py copy)")

lrn_tags = _learn.extract_concept_tags("SQLite database migration error in WAL mode")
test("learn.py extract returns list", isinstance(lrn_tags, list))
test("learn.py matches extract-knowledge.py output", lrn_tags == tags)

# Additional input shapes — catch future parity divergence early
_PARITY_CASES = [
    ("Python async await coroutine event loop thread", 5),
    ("migration migration migration rollback rollback schema", 5),
    ("oauth2 bearer-token api-key authentication header", 5),
    ("", 5),
    ("a an the", 5),
    ("database index query optimizer join", 3),
]
for _text, _k in _PARITY_CASES:
    _ek_out = _ek.extract_concept_tags(_text, top_k=_k)
    _lrn_out = _learn.extract_concept_tags(_text, top_k=_k)
    test(
        f"learn.py parity: {repr(_text[:40])} top_k={_k}",
        _lrn_out == _ek_out,
        f"learn={_lrn_out!r} ek={_ek_out!r}",
    )

section("extract_concept_tags — extraction logic (tag-entries.py copy)")

te_tags = _te.extract_concept_tags("SQLite database migration error in WAL mode")
test("tag-entries.py extract returns list", isinstance(te_tags, list))
test("tag-entries.py matches extract-knowledge.py output", te_tags == tags)

for _text, _k in _PARITY_CASES:
    _ek_out2 = _ek.extract_concept_tags(_text, top_k=_k)
    _te_out = _te.extract_concept_tags(_text, top_k=_k)
    test(
        f"tag-entries.py parity: {repr(_text[:40])} top_k={_k}",
        _te_out == _ek_out2,
        f"te={_te_out!r} ek={_ek_out2!r}",
    )

# ===========================================================================
# Section 2: Tag persistence — insert path
# ===========================================================================

section("Tag persistence — insert path (_auto_tag_entry)")

uri_ins = _new_uri()
db_ins = _make_db(uri_ins)

# Insert a fake entry
db_ins.execute(
    "INSERT INTO knowledge_entries (id, session_id, category, title, content) VALUES (1, 's1', 'mistake', 'WAL mode deadlock', 'SQLite WAL mode causes deadlock in concurrent migration scripts')"
)
db_ins.commit()

_learn._auto_tag_entry(db_ins, 1, "WAL mode deadlock", "SQLite WAL mode causes deadlock in concurrent migration scripts")

ect_rows = db_ins.execute("SELECT tag, source FROM entry_concept_tags WHERE entry_id = 1 ORDER BY tag").fetchall()
test("tags inserted for entry #1", len(ect_rows) > 0)
test("all inserted tags have source=auto", all(r["source"] == "auto" for r in ect_rows))
tags_written = {r["tag"] for r in ect_rows}
test("'sqlite' tag persisted", "sqlite" in tags_written or "wal" in tags_written or "deadlock" in tags_written)
test("at most 5 auto tags per entry", len(ect_rows) <= 5)

db_ins.close()

# ===========================================================================
# Section 3: Tag persistence — update path (stale tags replaced)
# ===========================================================================

section("Tag persistence — update path (stale auto tags replaced)")

uri_upd = _new_uri()
db_upd = _make_db(uri_upd)

db_upd.execute(
    "INSERT INTO knowledge_entries (id, session_id, category, title, content) VALUES (1, 's1', 'mistake', 'Cache error', 'Redis cache miss causes slow queries in production deployment')"
)
db_upd.commit()

# Simulate initial tagging
_learn._auto_tag_entry(db_upd, 1, "Cache error", "Redis cache miss causes slow queries in production deployment")
initial_tags = {r["tag"] for r in db_upd.execute(
    "SELECT tag FROM entry_concept_tags WHERE entry_id = 1 AND source = 'auto'"
).fetchall()}
test("initial tags present after first tag", len(initial_tags) > 0)
test("initial content reflects 'redis' or 'cache'", "redis" in initial_tags or "cache" in initial_tags)

# Simulate content update: completely different content
_learn._auto_tag_entry(db_upd, 1, "Cache error", "Python async await coroutine concurrency bug threadpool executor")
updated_tags = {r["tag"] for r in db_upd.execute(
    "SELECT tag FROM entry_concept_tags WHERE entry_id = 1 AND source = 'auto'"
).fetchall()}
test("tags updated after content change", len(updated_tags) > 0)
test("stale 'redis' tag removed after content update", "redis" not in updated_tags)
test("new content keywords present after update", "async" in updated_tags or "concurrency" in updated_tags or "python" in updated_tags or "coroutine" in updated_tags)
test("at most 5 auto tags after update", len(updated_tags) <= 5)

db_upd.close()

# ===========================================================================
# Section 3b: _auto_tag_entry — SAVEPOINT atomicity (failed insert preserves old tags)
# ===========================================================================

section("_auto_tag_entry — SAVEPOINT atomicity: failed insert preserves stale tags")

uri_sp = _new_uri()
db_sp = _make_db(uri_sp)

db_sp.execute(
    "INSERT INTO knowledge_entries (id, session_id, category, title, content) VALUES (1, 's1', 'mistake', 'DB error', 'SQLite WAL migration deadlock retry')"
)
db_sp.commit()

# Seed initial auto tags
db_sp.execute(
    "INSERT INTO entry_concept_tags (entry_id, tag, source) VALUES (1, 'sqlite', 'auto')"
)
db_sp.execute(
    "INSERT INTO entry_concept_tags (entry_id, tag, source) VALUES (1, 'migration', 'auto')"
)
db_sp.commit()

pre_tags = {r["tag"] for r in db_sp.execute(
    "SELECT tag FROM entry_concept_tags WHERE entry_id = 1 AND source = 'auto'"
).fetchall()}
test("pre-condition: stale tags exist", len(pre_tags) >= 2)


class _FailingInsertProxy:
    """Proxy that forwards all sqlite3.Connection calls, but raises on INSERT INTO entry_concept_tags."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __getattr__(self, name: str):
        return getattr(self._conn, name)

    def execute(self, sql: str, params=()):
        return self._conn.execute(sql, params)

    def executemany(self, sql: str, params):
        if "INSERT INTO entry_concept_tags" in sql:
            raise sqlite3.OperationalError("simulated insert failure")
        return self._conn.executemany(sql, params)


proxy_sp = _FailingInsertProxy(db_sp)
_learn._auto_tag_entry(proxy_sp, 1, "DB error", "SQLite WAL migration deadlock retry")

# Flush any pending transaction state — must NOT have deleted tags without replacing them
db_sp.commit()

post_tags = {r["tag"] for r in db_sp.execute(
    "SELECT tag FROM entry_concept_tags WHERE entry_id = 1 AND source = 'auto'"
).fetchall()}
test(
    "SAVEPOINT: stale tags survive failed executemany (not silently erased)",
    len(post_tags) >= 2,
    f"post_tags={post_tags!r}",
)

db_sp.close()

# ===========================================================================
# Section 4: _auto_tag_entry — graceful no-op when table absent
# ===========================================================================

section("_auto_tag_entry — graceful no-op when table absent")

uri_noect = _new_uri()
db_noect = sqlite3.connect(uri_noect, uri=True)
db_noect.row_factory = sqlite3.Row
db_noect.execute(_KE_SCHEMA)
db_noect.commit()

try:
    _learn._auto_tag_entry(db_noect, 1, "title", "content")
    test("no exception when entry_concept_tags absent", True)
except Exception as e:
    test("no exception when entry_concept_tags absent", False, str(e))

db_noect.close()

# ===========================================================================
# Section 5: Batch tagging — tag-entries.py run_batch_tag
# ===========================================================================

section("Batch tagging — run_batch_tag (tag-entries.py)")

uri_batch = _new_uri()
db_batch = _make_db(uri_batch)

for i in range(5):
    db_batch.execute(
        "INSERT INTO knowledge_entries (id, session_id, category, title, content) VALUES (?, 's1', 'mistake', ?, ?)",
        (i + 1, f"Entry {i+1}", f"This entry discusses database migration schema versioning rollback strategy {i}"),
    )
db_batch.commit()
# Keep db_batch open to keep the shared-memory URI alive

# Monkey-patch get_db to use our in-memory URI
orig_db_path = _te.DB_PATH
orig_get_db = _te.get_db


def _get_batch_db():
    db = sqlite3.connect(uri_batch, uri=True)
    db.row_factory = sqlite3.Row
    return db


_te.DB_PATH = Path("/fake/does-not-exist.db")
_te.get_db = _get_batch_db

try:
    stats = _te.run_batch_tag(retag_all=False, dry_run=False, limit=0, quiet=True)
    test("run_batch_tag returns dict", isinstance(stats, dict))
    test("available=True", stats.get("available") is True)
    test("processed > 0", stats.get("processed", 0) > 0)
    test("tagged > 0", stats.get("tagged", 0) > 0)
    test("errors == 0", stats.get("errors", 0) == 0)
    test("processed == tagged (all entries have content)", stats.get("processed") == stats.get("tagged"))

    # Second run — all already tagged, should find nothing (retag_all=False)
    stats2 = _te.run_batch_tag(retag_all=False, dry_run=False, limit=0, quiet=True)
    test("second run processes 0 when nothing untagged", stats2.get("processed", -1) == 0)

    # Run with --all flag: should re-tag everything
    stats3 = _te.run_batch_tag(retag_all=True, dry_run=False, limit=0, quiet=True)
    test("retag_all=True re-processes all entries", stats3.get("processed", 0) == 5)
finally:
    _te.DB_PATH = orig_db_path
    _te.get_db = orig_get_db
    db_batch.close()

section("Batch tagging — run_batch_tag dry-run does not write")

uri_dr = _new_uri()
db_dr = _make_db(uri_dr)
db_dr.execute(
    "INSERT INTO knowledge_entries (id, session_id, category, title, content) VALUES (1, 's1', 'mistake', 'Schema rollback', 'Database schema migration rollback versioning strategy')"
)
db_dr.commit()
# Keep db_dr open


def _get_dr_db():
    db = sqlite3.connect(uri_dr, uri=True)
    db.row_factory = sqlite3.Row
    return db


_te.get_db = _get_dr_db
_te.DB_PATH = Path("/fake/does-not-exist.db")

try:
    dr_stats = _te.run_batch_tag(retag_all=False, dry_run=True, limit=0, quiet=True)
    test("dry_run returns processed > 0", dr_stats.get("processed", 0) > 0)

    # Verify nothing was written
    db_check = _get_dr_db()
    written = db_check.execute("SELECT COUNT(*) FROM entry_concept_tags").fetchone()[0]
    db_check.close()
    test("dry_run writes nothing to DB", written == 0)
finally:
    _te.DB_PATH = orig_db_path
    _te.get_db = orig_get_db
    db_dr.close()

section("Batch tagging — run_stats returns coverage")

uri_stat = _new_uri()
db_stat = _make_db(uri_stat)
for i in range(4):
    db_stat.execute(
        "INSERT INTO knowledge_entries (id, session_id, category, title, content) VALUES (?, 's1', 'mistake', ?, ?)",
        (i + 1, f"Title {i+1}", "database migration schema error rollback"),
    )
# Tag only 2 of the 4 entries
now_s = time.strftime("%Y-%m-%dT%H:%M:%S")
db_stat.execute("INSERT INTO entry_concept_tags (entry_id, tag, source, tagged_at) VALUES (1, 'migration', 'auto', ?)", (now_s,))
db_stat.execute("INSERT INTO entry_concept_tags (entry_id, tag, source, tagged_at) VALUES (2, 'schema', 'auto', ?)", (now_s,))
db_stat.commit()
# Keep db_stat open to preserve the shared-memory URI


def _get_stat_db():
    db = sqlite3.connect(uri_stat, uri=True)
    db.row_factory = sqlite3.Row
    return db


_te.get_db = _get_stat_db
_te.DB_PATH = Path("/fake/does-not-exist.db")

try:
    s = _te.run_stats()
    test("run_stats returns dict", isinstance(s, dict))
    test("available=True", s.get("available") is True)
    test("total_entries=4", s.get("total_entries") == 4)
    test("tagged_entries=2 (only 2 tagged)", s.get("tagged_entries") == 2)
    test("coverage_pct=50.0", s.get("coverage_pct") == 50.0)
    test("top_tags is list", isinstance(s.get("top_tags"), list))
finally:
    _te.DB_PATH = orig_db_path
    _te.get_db = orig_get_db
    db_stat.close()

# ===========================================================================
# Section 6:

section("knowledge-health.py — concept_tag_coverage_pct (informational stat)")

_ECT_HEALTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS entry_concept_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL,
    tag TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'auto',
    tagged_at TEXT DEFAULT (datetime('now')),
    UNIQUE(entry_id, tag)
)
"""

_RELATIONS_SCHEMA = "CREATE TABLE knowledge_relations (id INTEGER PRIMARY KEY, source_id INTEGER, target_id INTEGER, relation_type TEXT)"
_ENTITY_REL_SCHEMA = "CREATE TABLE entity_relations (id INTEGER PRIMARY KEY, source TEXT, target TEXT, relation_type TEXT)"
_EMBEDDINGS_SCHEMA = "CREATE TABLE embeddings (id INTEGER PRIMARY KEY, source_type TEXT DEFAULT 'knowledge', source_id INTEGER, vector BLOB)"
_SV_SCHEMA = "CREATE TABLE schema_version (version INTEGER, name TEXT)"

_KE_HEALTH_SCHEMA = """
CREATE TABLE knowledge_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    document_id INTEGER,
    category TEXT,
    title TEXT,
    content TEXT,
    tags TEXT,
    confidence REAL DEFAULT 0.5,
    occurrence_count INTEGER DEFAULT 1,
    first_seen TEXT,
    last_seen TEXT,
    source TEXT,
    topic_key TEXT,
    revision_count INTEGER DEFAULT 0,
    content_hash TEXT,
    wing TEXT,
    room TEXT,
    facts TEXT,
    est_tokens INTEGER,
    task_id TEXT,
    affected_files TEXT,
    source_section TEXT,
    source_file TEXT,
    start_line INTEGER,
    end_line INTEGER,
    code_language TEXT,
    code_snippet TEXT,
    stable_id TEXT
)
"""


def _make_health_db(uri: str, with_ect: bool = True) -> sqlite3.Connection:
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    db.execute(_KE_HEALTH_SCHEMA)
    db.execute(_RELATIONS_SCHEMA)
    db.execute(_ENTITY_REL_SCHEMA)
    db.execute(_EMBEDDINGS_SCHEMA)
    db.execute(_SV_SCHEMA)
    if with_ect:
        db.execute(_ECT_HEALTH_SCHEMA)
    db.commit()
    return db


def _health_get_db_factory(uri: str):
    def _get_db():
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    return _get_db


orig_kh_get_db = _kh.get_db

uri_kh = _new_uri()
db_kh = _make_health_db(uri_kh, with_ect=True)

for i in range(6):
    db_kh.execute(
        """INSERT INTO knowledge_entries (category, title, content, confidence, occurrence_count,
           first_seen, last_seen, session_id, affected_files)
           VALUES ('mistake', ?, '', 0.7, 1, '2025-01-01', '2025-01-01', 's1', '[]')""",
        (f"Entry {i+1}",),
    )
db_kh.commit()

now_kh = time.strftime("%Y-%m-%dT%H:%M:%S")
# Tag 3 out of 6 entries
for eid in (1, 2, 3):
    db_kh.execute(
        "INSERT INTO entry_concept_tags (entry_id, tag, source, tagged_at) VALUES (?, 'tag', 'auto', ?)",
        (eid, now_kh),
    )
db_kh.commit()

_kh.get_db = _health_get_db_factory(uri_kh)
_kh.DB_PATH = Path("/nonexistent/fake.db")

try:
    health = _kh.compute_health()

    test("concept_tag_coverage_pct key present in compute_health result", "concept_tag_coverage_pct" in health)
    test("concept_tag_coverage_pct is float/int", isinstance(health.get("concept_tag_coverage_pct"), (int, float)))
    test("concept_tag_coverage_pct == 50.0 (3/6 tagged)", health.get("concept_tag_coverage_pct") == 50.0)

    # Verify weighted score NOT affected (score == sum of existing 6 subscores)
    subscores = health.get("subscores", {})
    test("subscores still has exactly 6 keys", len(subscores) == 6)
    expected_keys = {"categorization", "learning_curve", "freshness", "relation_density", "embedding_coverage", "confidence_quality"}
    test("subscores keys unchanged", set(subscores.keys()) == expected_keys)
    computed_score = sum(subscores.values())
    test("health score equals sum of 6 subscores (concept_tag not in score)", abs(health["score"] - round(computed_score, 1)) < 0.1)

finally:
    _kh.get_db = orig_kh_get_db

db_kh.close()

section("knowledge-health.py — concept_tag_coverage_pct = 0 when table absent")

uri_noect_kh = _new_uri()
db_noect_kh = _make_health_db(uri_noect_kh, with_ect=False)
for i in range(3):
    db_noect_kh.execute(
        "INSERT INTO knowledge_entries (category, title, content, confidence, occurrence_count, first_seen, last_seen, session_id, affected_files) VALUES ('mistake', ?, '', 0.7, 1, '2025-01-01', '2025-01-01', 's1', '[]')",
        (f"Entry {i+1}",),
    )
db_noect_kh.commit()

_kh.get_db = _health_get_db_factory(uri_noect_kh)

try:
    h_noect = _kh.compute_health()
    test("concept_tag_coverage_pct present even without table", "concept_tag_coverage_pct" in h_noect)
    test("concept_tag_coverage_pct = 0.0 when table absent", h_noect.get("concept_tag_coverage_pct") == 0.0)
    test("health score unaffected when table absent", "score" in h_noect and 0 <= h_noect["score"] <= 100)
finally:
    _kh.get_db = orig_kh_get_db

db_noect_kh.close()

# ===========================================================================
# Final summary
# ===========================================================================
print(f"\n{'='*60}")
print(f"Results: {_PASS} passed, {_FAIL} failed")
if _ERRORS:
    print("\nFailures:")
    for e in _ERRORS:
        print(e)
print("=" * 60)
sys.exit(0 if _FAIL == 0 else 1)
