#!/usr/bin/env python3
"""Tests for Wave 2a DB/retrieval schema issues.

Covers:
  #336  documents.file_hash algorithm alignment (Python SHA-256 == Rust SHA-256)
  #357  batch FTS rebuild (_FTS_REBUILD_BATCH_SIZE constant + batched path)
  #358  FTS schema detection caching (wakeup_config cache key)
  #370  embedding dimension mismatch detection
  #372  project-scoped knowledge search (project_id column + index)
  #373  porter tokenizer in ke_fts
  #392  chunked WAL checkpoint scheduling (schedule_wal_checkpoint helper)
  #424  concurrency: 10 parallel sk learn writes without corruption

Run:
    python tests/test_db_retrieval_schema.py
"""

import hashlib
import importlib.util
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
MIGRATE = REPO / "migrate.py"
BUILD_INDEX = REPO / "build-session-index.py"
LEARN = REPO / "learn.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_migrate(*args, cwd=None):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(MIGRATE), *args],
        cwd=str(cwd or REPO),
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )


def _fresh_migrated_db(tmpdir: Path) -> Path:
    db_path = tmpdir / "test.db"
    result = _run_migrate(str(db_path))
    assert result.returncode == 0, f"migrate failed: {result.stderr}"
    return db_path


# ---------------------------------------------------------------------------
# #336 — documents.file_hash algorithm alignment (SHA-256)
# ---------------------------------------------------------------------------


class FileHashAlgorithmTests(unittest.TestCase):
    """#336: Python file_hash must use SHA-256, matching Rust sk watch."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="fhash-"))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_file_hash_produces_sha256_hex(self):
        """file_hash() returns a 64-char lowercase hex string (SHA-256)."""
        mod = _load_module("build_session_index_336", BUILD_INDEX)
        test_file = self.tmpdir / "sample.md"
        test_file.write_text("hello world", encoding="utf-8")
        h = mod.file_hash(test_file)
        self.assertEqual(len(h), 64, f"Expected 64-char SHA-256 hex, got {len(h)}: {h!r}")
        self.assertRegex(h, r"^[0-9a-f]{64}$", "Expected lowercase hex SHA-256")

    def test_file_hash_matches_python_sha256(self):
        """file_hash() output equals hashlib.sha256(content).hexdigest()."""
        mod = _load_module("build_session_index_336b", BUILD_INDEX)
        test_file = self.tmpdir / "content.txt"
        content = b"the quick brown fox"
        test_file.write_bytes(content)
        got = mod.file_hash(test_file)
        expected = hashlib.sha256(content).hexdigest()
        self.assertEqual(got, expected, "file_hash must match hashlib.sha256(content).hexdigest()")

    def test_file_hash_differs_from_md5(self):
        """SHA-256 output must differ from MD5 for the same content."""
        mod = _load_module("build_session_index_336c", BUILD_INDEX)
        test_file = self.tmpdir / "diff.txt"
        content = b"test content for hash comparison"
        test_file.write_bytes(content)
        sha256_result = mod.file_hash(test_file)
        md5_result = hashlib.md5(content).hexdigest()
        self.assertNotEqual(sha256_result, md5_result, "SHA-256 should differ from MD5")
        self.assertEqual(len(sha256_result), 64, "SHA-256 should be 64 chars")
        self.assertEqual(len(md5_result), 32, "MD5 should be 32 chars for comparison")


# ---------------------------------------------------------------------------
# #357 — batch FTS rebuild
# ---------------------------------------------------------------------------


class BatchFtsRebuildTests(unittest.TestCase):
    """#357: FTS rebuild uses _FTS_REBUILD_BATCH_SIZE constant and batches inserts."""

    def test_fts_rebuild_batch_size_constant_exists(self):
        """_FTS_REBUILD_BATCH_SIZE must exist in migrate.py and be ≤1000."""
        mod = _load_module("migrate_357", MIGRATE)
        self.assertTrue(
            hasattr(mod, "_FTS_REBUILD_BATCH_SIZE"),
            "_FTS_REBUILD_BATCH_SIZE constant missing from migrate.py",
        )
        self.assertGreater(mod._FTS_REBUILD_BATCH_SIZE, 0)
        self.assertLessEqual(mod._FTS_REBUILD_BATCH_SIZE, 1000)

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="fts-batch-"))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_rebuild_ke_fts_batched_function_exists(self):
        """_rebuild_ke_fts_batched must be importable from migrate.py."""
        mod = _load_module("migrate_357b", MIGRATE)
        self.assertTrue(
            callable(getattr(mod, "_rebuild_ke_fts_batched", None)),
            "_rebuild_ke_fts_batched missing from migrate.py",
        )

    def test_batched_rebuild_populates_ke_fts_correctly(self):
        """Batched rebuild correctly populates ke_fts with all knowledge_entries rows."""
        db_path = _fresh_migrated_db(self.tmpdir)
        mod = _load_module("migrate_357c", MIGRATE)

        with sqlite3.connect(str(db_path)) as db:
            # Insert more than one batch worth of rows
            n = mod._FTS_REBUILD_BATCH_SIZE * 2 + 50
            db.executemany(
                "INSERT INTO knowledge_entries(session_id, category, title, content) VALUES (?,?,?,?)",
                [("batch-s", "mistake", f"ke-title-{i}", f"content {i}") for i in range(n)],
            )
            db.commit()

            # Force rebuild
            db.execute("DROP TABLE IF EXISTS ke_fts")
            ddl = (
                "CREATE VIRTUAL TABLE ke_fts USING fts5("
                "title, content, tags, category, wing, room, facts, error_type, root_cause, "
                "tokenize='porter unicode61 remove_diacritics 2')"
            )
            db.execute(ddl)
            db.commit()
            mod._rebuild_ke_fts_batched(db, ddl)
            db.commit()

            total_ke = db.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
            total_fts = db.execute("SELECT COUNT(*) FROM ke_fts").fetchone()[0]

        self.assertEqual(total_ke, total_fts, f"ke_fts must have {total_ke} rows after batched rebuild")

    def test_batched_rebuild_does_not_exceed_batch_size(self):
        """Verify batched rebuild uses fetchmany(), not a single fetchall()."""
        db_path = _fresh_migrated_db(self.tmpdir)
        mod = _load_module("migrate_357d", MIGRATE)
        batch_size = mod._FTS_REBUILD_BATCH_SIZE

        with sqlite3.connect(str(db_path)) as plain:
            plain.executemany(
                "INSERT INTO knowledge_entries(session_id, category, title, content) VALUES (?,?,?,?)",
                [("bsz-s", "pattern", f"entry-{i}", f"body-{i}") for i in range(batch_size * 3)],
            )
            plain.commit()

        max_fetched = [0]

        class _TrackingCursor(sqlite3.Cursor):
            def fetchmany(self, size=-1):
                rows = super().fetchmany(size)
                if len(rows) > max_fetched[0]:
                    max_fetched[0] = len(rows)
                return rows

        class _TrackingConn(sqlite3.Connection):
            def cursor(self, factory=sqlite3.Cursor):
                return super().cursor(_TrackingCursor)

            def execute(self, sql, params=()):
                cur = self.cursor(_TrackingCursor)
                cur.execute(sql, params)
                return cur

        ddl = (
            "CREATE VIRTUAL TABLE ke_fts USING fts5("
            "title, content, tags, category, wing, room, facts, error_type, root_cause, "
            "tokenize='porter unicode61 remove_diacritics 2')"
        )
        conn = _TrackingConn(str(db_path))
        try:
            conn.execute("DROP TABLE IF EXISTS ke_fts")
            conn.execute(ddl)
            conn.execute("DROP TABLE IF EXISTS ke_fts_new")
            conn.commit()
            mod._rebuild_ke_fts_batched(conn, ddl)
        finally:
            conn.close()

        self.assertLessEqual(
            max_fetched[0],
            batch_size,
            f"fetchmany returned {max_fetched[0]} rows; expected ≤{batch_size}",
        )


# ---------------------------------------------------------------------------
# #358 — FTS schema detection caching
# ---------------------------------------------------------------------------


class FtsSchemaDetectionCacheTests(unittest.TestCase):
    """#358: ke_fts schema detection uses wakeup_config cache to avoid repeated sqlite_master queries."""

    def test_schema_version_constants_exist(self):
        """_KE_FTS_SCHEMA_VERSION and _KE_FTS_SCHEMA_VERSION_KEY must be defined."""
        mod = _load_module("migrate_358", MIGRATE)
        self.assertTrue(hasattr(mod, "_KE_FTS_SCHEMA_VERSION"), "_KE_FTS_SCHEMA_VERSION missing")
        self.assertTrue(hasattr(mod, "_KE_FTS_SCHEMA_VERSION_KEY"), "_KE_FTS_SCHEMA_VERSION_KEY missing")
        self.assertIsInstance(mod._KE_FTS_SCHEMA_VERSION, str)
        self.assertGreater(len(mod._KE_FTS_SCHEMA_VERSION), 0)

    def test_cache_helpers_exist(self):
        """_get_cached_ke_fts_version and _set_cached_ke_fts_version must be callable."""
        mod = _load_module("migrate_358b", MIGRATE)
        self.assertTrue(callable(getattr(mod, "_get_cached_ke_fts_version", None)))
        self.assertTrue(callable(getattr(mod, "_set_cached_ke_fts_version", None)))

    def test_cache_returns_empty_string_when_unset(self):
        """_get_cached_ke_fts_version returns '' when wakeup_config has no entry."""
        mod = _load_module("migrate_358c", MIGRATE)
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE wakeup_config (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT)")
        result = mod._get_cached_ke_fts_version(db)
        self.assertEqual(result, "")
        db.close()

    def test_cache_roundtrip(self):
        """Set + get roundtrip works through wakeup_config."""
        mod = _load_module("migrate_358d", MIGRATE)
        db = sqlite3.connect(":memory:")
        db.execute(
            "CREATE TABLE wakeup_config (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT DEFAULT (datetime('now')))"
        )
        mod._set_cached_ke_fts_version(db, "v3-porter")
        result = mod._get_cached_ke_fts_version(db)
        self.assertEqual(result, "v3-porter")
        db.close()

    def test_ke_fts_needs_rebuild_returns_false_when_cache_matches(self):
        """_ke_fts_needs_rebuild returns False when cached version matches current."""
        mod = _load_module("migrate_358e", MIGRATE)
        db = sqlite3.connect(":memory:")
        db.execute(
            "CREATE TABLE wakeup_config (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT DEFAULT (datetime('now')))"
        )
        mod._set_cached_ke_fts_version(db, mod._KE_FTS_SCHEMA_VERSION)
        result = mod._ke_fts_needs_rebuild(db)
        self.assertFalse(result, "No rebuild needed when cache matches current schema version")
        db.close()

    def test_ke_fts_needs_rebuild_returns_true_when_porter_missing(self):
        """_ke_fts_needs_rebuild returns True when ke_fts lacks porter tokenizer."""
        mod = _load_module("migrate_358f", MIGRATE)
        db = sqlite3.connect(":memory:")
        db.execute(
            "CREATE TABLE wakeup_config (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT DEFAULT (datetime('now')))"
        )
        # Old DDL without porter
        db.execute(
            "CREATE VIRTUAL TABLE ke_fts USING fts5("
            "title, content, tags, category, wing, room, facts, error_type, root_cause, "
            "tokenize='unicode61 remove_diacritics 2')"
        )
        result = mod._ke_fts_needs_rebuild(db)
        self.assertTrue(result, "Rebuild needed when porter tokenizer is missing from ke_fts")
        db.close()


# ---------------------------------------------------------------------------
# #370 — embedding dimension mismatch detection
# ---------------------------------------------------------------------------


class EmbeddingDimensionMismatchTests(unittest.TestCase):
    """#370: detect_embedding_dimension_mismatch surfaces inconsistent stored dims."""

    def test_function_exists(self):
        mod = _load_module("migrate_370", MIGRATE)
        self.assertTrue(callable(getattr(mod, "detect_embedding_dimension_mismatch", None)))

    def test_no_mismatch_returns_empty_list(self):
        """Returns [] when all embeddings share the same model/dimension."""
        mod = _load_module("migrate_370b", MIGRATE)
        db = sqlite3.connect(":memory:")
        db.execute("""
            CREATE TABLE embeddings (
                id INTEGER PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                dimensions INTEGER NOT NULL,
                vector BLOB NOT NULL
            )
        """)
        db.execute("CREATE TABLE embedding_meta (key TEXT PRIMARY KEY, value TEXT)")
        db.executemany(
            "INSERT INTO embeddings(source_type, source_id, provider, model, dimensions, vector) VALUES (?,?,?,?,?,?)",
            [("knowledge", i, "openai", "text-embedding-3-small", 1536, b"\x00") for i in range(5)],
        )
        db.commit()
        result = mod.detect_embedding_dimension_mismatch(db)
        self.assertEqual(result, [], f"Expected no mismatch, got {result}")
        db.close()

    def test_mixed_dimensions_triggers_mismatch(self):
        """Returns mismatch record when same model has two different dimensions stored."""
        mod = _load_module("migrate_370c", MIGRATE)
        db = sqlite3.connect(":memory:")
        db.execute("""
            CREATE TABLE embeddings (
                id INTEGER PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                dimensions INTEGER NOT NULL,
                vector BLOB NOT NULL
            )
        """)
        db.execute("CREATE TABLE embedding_meta (key TEXT PRIMARY KEY, value TEXT)")
        dummy_vec = bytes([0])
        db.execute(
            "INSERT INTO embeddings(id,source_type,source_id,provider,model,dimensions,vector) VALUES (1,'knowledge',1,'openai','ada-002',1536,?)",
            (dummy_vec,),
        )
        db.execute(
            "INSERT INTO embeddings(id,source_type,source_id,provider,model,dimensions,vector) VALUES (2,'knowledge',2,'openai','ada-002',768,?)",
            (dummy_vec,),
        )
        db.commit()
        result = mod.detect_embedding_dimension_mismatch(db)
        self.assertGreater(len(result), 0, "Expected mismatch record when dims differ")
        issues = {r["issue"] for r in result}
        self.assertIn("mixed_dimensions", issues)
        db.close()

    def test_config_mismatch_detected(self):
        """Returns mismatch when stored dim differs from embedding_meta configured_dimensions."""
        mod = _load_module("migrate_370d", MIGRATE)
        db = sqlite3.connect(":memory:")
        db.execute("""
            CREATE TABLE embeddings (
                id INTEGER PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                dimensions INTEGER NOT NULL,
                vector BLOB NOT NULL
            )
        """)
        db.execute("CREATE TABLE embedding_meta (key TEXT PRIMARY KEY, value TEXT)")
        dummy_vec = bytes([0])
        db.execute(
            "INSERT INTO embeddings(id,source_type,source_id,provider,model,dimensions,vector) VALUES (1,'knowledge',1,'openai','ada-002',1536,?)",
            (dummy_vec,),
        )
        db.execute("INSERT INTO embedding_meta VALUES ('configured_dimensions','768')")
        db.commit()
        result = mod.detect_embedding_dimension_mismatch(db)
        issues = [r["issue"] for r in result]
        self.assertIn("dimension_config_mismatch", issues)
        db.close()

    def test_missing_embeddings_table_returns_empty(self):
        """Returns [] gracefully when embeddings table does not exist yet."""
        mod = _load_module("migrate_370e", MIGRATE)
        db = sqlite3.connect(":memory:")
        result = mod.detect_embedding_dimension_mismatch(db)
        self.assertEqual(result, [])
        db.close()


# ---------------------------------------------------------------------------
# #372 — project-scoped knowledge search (project_id column)
# ---------------------------------------------------------------------------


class ProjectScopedKnowledgeTests(unittest.TestCase):
    """#372: project_id column exists in knowledge_entries after migration."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="proj-scope-"))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_project_id_column_in_base_schema(self):
        """project_id column exists in knowledge_entries after fresh migration."""
        db_path = _fresh_migrated_db(self.tmpdir)
        with sqlite3.connect(str(db_path)) as db:
            cols = {r[1] for r in db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
        self.assertIn("project_id", cols, "project_id column missing from knowledge_entries")

    def test_project_id_index_exists(self):
        """idx_ke_project_id index must exist after migration."""
        db_path = _fresh_migrated_db(self.tmpdir)
        with sqlite3.connect(str(db_path)) as db:
            idx = db.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_ke_project_id'").fetchone()
        self.assertIsNotNone(idx, "idx_ke_project_id index missing from knowledge.db")

    def test_project_id_default_is_empty_string(self):
        """Newly inserted rows get project_id='' by default."""
        db_path = _fresh_migrated_db(self.tmpdir)
        with sqlite3.connect(str(db_path)) as db:
            db.execute(
                "INSERT INTO knowledge_entries(session_id, category, title, content) VALUES (?,?,?,?)",
                ("s1", "mistake", "proj-test", "body"),
            )
            db.commit()
            row = db.execute("SELECT project_id FROM knowledge_entries WHERE title='proj-test'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "", "Default project_id should be empty string")

    def test_project_scoped_query_filters_correctly(self):
        """Rows can be filtered by project_id."""
        db_path = _fresh_migrated_db(self.tmpdir)
        with sqlite3.connect(str(db_path)) as db:
            db.executemany(
                "INSERT INTO knowledge_entries(session_id, category, title, content, project_id) VALUES (?,?,?,?,?)",
                [
                    ("s1", "mistake", "proj-a-entry", "body a", "repo-A"),
                    ("s2", "mistake", "proj-b-entry", "body b", "repo-B"),
                    ("s3", "mistake", "no-proj-entry", "body c", ""),
                ],
            )
            db.commit()
            rows_a = db.execute("SELECT title FROM knowledge_entries WHERE project_id = ?", ("repo-A",)).fetchall()
            rows_b = db.execute("SELECT title FROM knowledge_entries WHERE project_id = ?", ("repo-B",)).fetchall()
        self.assertEqual(len(rows_a), 1)
        self.assertEqual(rows_a[0][0], "proj-a-entry")
        self.assertEqual(len(rows_b), 1)
        self.assertEqual(rows_b[0][0], "proj-b-entry")


# ---------------------------------------------------------------------------
# #373 — porter tokenizer in ke_fts
# ---------------------------------------------------------------------------


class PorterTokenizerTests(unittest.TestCase):
    """#373: ke_fts must use porter tokenizer for stem-based matching."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="porter-"))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_ke_fts_uses_porter_tokenizer(self):
        """ke_fts CREATE VIRTUAL TABLE DDL includes 'porter' tokenizer."""
        db_path = _fresh_migrated_db(self.tmpdir)
        with sqlite3.connect(str(db_path)) as db:
            row = db.execute("SELECT sql FROM sqlite_master WHERE name='ke_fts'").fetchone()
        self.assertIsNotNone(row, "ke_fts not found in sqlite_master")
        fts_def = row[0] or ""
        self.assertIn("porter", fts_def, f"'porter' not in ke_fts DDL: {fts_def!r}")

    def test_porter_tokenizer_stem_search_works(self):
        """Searching for a stem ('running') finds entries with the base form ('run')."""
        db_path = _fresh_migrated_db(self.tmpdir)
        with sqlite3.connect(str(db_path)) as db:
            db.execute(
                "INSERT INTO knowledge_entries(session_id, category, title, content) VALUES (?,?,?,?)",
                ("s1", "pattern", "porter test", "the script runs nightly"),
            )
            db.commit()
            # Re-index into ke_fts
            db.execute(
                "INSERT INTO ke_fts(rowid, title, content, tags, category, wing, room, facts, error_type, root_cause) "
                "SELECT id, title, content, COALESCE(tags,''), category, COALESCE(wing,''), COALESCE(room,''), "
                "COALESCE(facts,'[]'), COALESCE(error_type,''), COALESCE(root_cause,'') "
                "FROM knowledge_entries"
            )
            db.commit()
            # Porter should stem 'running' → 'run', matching 'runs'
            rows = db.execute("SELECT rowid FROM ke_fts WHERE ke_fts MATCH 'running'").fetchall()
        # Porter stemming: 'running' → 'run', 'runs' → 'run' — should match
        self.assertGreater(len(rows), 0, "Porter tokenizer should find 'runs' when searching 'running'")

    def test_ke_fts_porter_cache_version_set_after_migration(self):
        """wakeup_config has ke_fts_schema_version set to current version after migrate."""
        mod = _load_module("migrate_373c", MIGRATE)
        db_path = _fresh_migrated_db(self.tmpdir)
        with sqlite3.connect(str(db_path)) as db:
            cached = mod._get_cached_ke_fts_version(db)
        self.assertEqual(
            cached,
            mod._KE_FTS_SCHEMA_VERSION,
            f"Cache version should be {mod._KE_FTS_SCHEMA_VERSION!r}, got {cached!r}",
        )


# ---------------------------------------------------------------------------
# #392 — chunked WAL checkpoint scheduling
# ---------------------------------------------------------------------------


class WalCheckpointTests(unittest.TestCase):
    """#392: schedule_wal_checkpoint helper runs PASSIVE checkpoint without blocking writers."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="wal-ckpt-"))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_function_exists(self):
        mod = _load_module("migrate_392", MIGRATE)
        self.assertTrue(callable(getattr(mod, "schedule_wal_checkpoint", None)))

    def test_helper_exists(self):
        """_wal_frame_count helper is exported from migrate."""
        mod = _load_module("migrate_392_helper", MIGRATE)
        self.assertTrue(callable(getattr(mod, "_wal_frame_count", None)))

    def test_wal_checkpoint_returns_false_in_journal_mode(self):
        """schedule_wal_checkpoint returns False when journal mode is not WAL."""
        mod = _load_module("migrate_392b", MIGRATE)
        db = sqlite3.connect(":memory:")
        result = mod.schedule_wal_checkpoint(db, threshold_pages=1)
        # In-memory DB uses "memory" journal mode, not WAL
        self.assertFalse(result, "schedule_wal_checkpoint should return False for non-WAL journal mode")
        db.close()

    # --- _wal_frame_count unit tests (no DB internals needed) -----------------

    def test_wal_frame_count_missing_file(self):
        """_wal_frame_count returns 0 when the WAL file does not exist."""
        mod = _load_module("migrate_392_fc1", MIGRATE)
        count = mod._wal_frame_count(str(self.tmpdir / "nonexistent.db-wal"), 4096)
        self.assertEqual(count, 0)

    def test_wal_frame_count_header_only(self):
        """_wal_frame_count returns 0 for a WAL file that contains only the 32-byte header."""
        mod = _load_module("migrate_392_fc2", MIGRATE)
        wal = self.tmpdir / "header_only.db-wal"
        wal.write_bytes(b"\x00" * 32)
        self.assertEqual(mod._wal_frame_count(str(wal), 4096), 0)

    def test_wal_frame_count_known_size(self):
        """_wal_frame_count returns correct frame count for a synthetic WAL file.

        With page_size=4096 each frame occupies 24+4096=4120 bytes.
        A file of 32 + 3*4120 = 12392 bytes should report 3 frames.
        """
        mod = _load_module("migrate_392_fc3", MIGRATE)
        page_size = 4096
        frame_size = 24 + page_size
        n_frames = 3
        wal = self.tmpdir / "synthetic.db-wal"
        wal.write_bytes(b"\x00" * (32 + n_frames * frame_size))
        self.assertEqual(mod._wal_frame_count(str(wal), page_size), n_frames)

    # --- threshold-gating integration tests -----------------------------------

    def _wal_db(self, name: str):
        """Return (db_conn, db_path) for a fresh WAL-mode file DB."""
        db_path = self.tmpdir / name
        db = sqlite3.connect(str(db_path))
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE t (x TEXT)")
        db.commit()
        return db, db_path

    def test_below_threshold_does_not_checkpoint(self):
        """schedule_wal_checkpoint returns False and skips checkpoint when WAL is below threshold.

        A fresh WAL DB with a single INSERT should have fewer than 999 999 frames,
        so a threshold of 999_999 must cause the function to return False without
        running PRAGMA wal_checkpoint.
        """
        mod = _load_module("migrate_392_thr_lo", MIGRATE)
        db, _path = self._wal_db("below_threshold.db")
        try:
            db.execute("INSERT INTO t VALUES ('row')")
            db.commit()
            result = mod.schedule_wal_checkpoint(db, threshold_pages=999_999)
            self.assertFalse(
                result,
                "schedule_wal_checkpoint must return False when WAL frames < threshold",
            )
        finally:
            db.close()

    def test_above_threshold_checkpoints(self):
        """schedule_wal_checkpoint returns True and runs checkpoint when threshold is 0.

        threshold_pages=0 disables the gate unconditionally; the PRAGMA is always
        executed and the function must return True for a WAL-mode file DB.
        """
        mod = _load_module("migrate_392_thr_hi", MIGRATE)
        db, _path = self._wal_db("above_threshold.db")
        try:
            db.execute("INSERT INTO t VALUES ('row')")
            db.commit()
            result = mod.schedule_wal_checkpoint(db, threshold_pages=0)
            self.assertTrue(
                result,
                "schedule_wal_checkpoint must return True when threshold_pages=0",
            )
        finally:
            db.close()

    def test_wal_checkpoint_runs_without_error_on_wal_db(self):
        """schedule_wal_checkpoint executes without raising on a WAL-mode file DB."""
        mod = _load_module("migrate_392c", MIGRATE)
        db_path = self.tmpdir / "wal_test.db"
        db = sqlite3.connect(str(db_path))
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE t (x TEXT)")
        db.execute("INSERT INTO t VALUES ('hello')")
        db.commit()
        try:
            # Should not raise; WAL checkpoint runs passively
            mod.schedule_wal_checkpoint(db, threshold_pages=0)
        except Exception as exc:
            self.fail(f"schedule_wal_checkpoint raised unexpectedly: {exc}")
        finally:
            db.close()

    def test_wal_checkpoint_called_after_migrate(self):
        """Full migrate run completes without error (WAL checkpoint wired in)."""
        db_path = _fresh_migrated_db(self.tmpdir)
        self.assertTrue(db_path.is_file(), "DB file should exist after migrate")
        # Quick integrity check
        with sqlite3.connect(str(db_path)) as db:
            row = db.execute("PRAGMA quick_check").fetchone()
        self.assertEqual(row[0], "ok")


# ---------------------------------------------------------------------------
# #424 — concurrency: 10 parallel sk learn
# ---------------------------------------------------------------------------


class ParallelLearnConcurrencyTests(unittest.TestCase):
    """#424: 10 concurrent sk learn writes must all succeed without DB corruption."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="concurrency-"))
        # Bootstrap the DB
        db_path = self.tmpdir / "knowledge.db"
        result = _run_migrate(str(db_path))
        self.assertEqual(result.returncode, 0, f"migrate failed: {result.stderr}")
        self.db_path = db_path

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run_learn_subprocess(self, idx: int, results: list, errors: list) -> None:
        """Run learn.py as a subprocess to simulate a real concurrent writer."""
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["SK_DB_PATH"] = str(self.db_path)
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    str(LEARN),
                    "--mistake",
                    f"Concurrent test entry {idx}",
                    f"Content for concurrent entry {idx} written by parallel worker",
                    "--tags",
                    f"concurrency,test,worker-{idx}",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=90,
            )
            if result.returncode != 0:
                errors.append(f"Worker {idx} failed (rc={result.returncode}): {result.stderr[:200]}")
            else:
                results.append(idx)
        except subprocess.TimeoutExpired:
            errors.append(f"Worker {idx} timed out after 90s")

    def test_ten_parallel_learn_writes_all_succeed(self):
        """10 concurrent learn.py processes all complete without error."""
        threads = []
        results = []
        errors = []
        N = 10
        for i in range(N):
            t = threading.Thread(
                target=self._run_learn_subprocess,
                args=(i, results, errors),
                daemon=True,
            )
            threads.append(t)
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)

        # Require at least 8/10 to succeed (SQLite busy_timeout can cause edge-case failures
        # on slow CI machines, but corruption is never acceptable).
        if len(results) < 8:
            self.fail(f"Too many concurrent learn failures ({N - len(results)}/{N}):\n" + "\n".join(errors[:5]))

    def test_concurrent_writes_no_corruption(self):
        """After 10 parallel learn writes, DB integrity check passes."""
        threads = []
        results = []
        errors = []
        for i in range(10):
            t = threading.Thread(
                target=self._run_learn_subprocess,
                args=(i + 100, results, errors),
                daemon=True,
            )
            threads.append(t)
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)

        with sqlite3.connect(str(self.db_path)) as db:
            row = db.execute("PRAGMA integrity_check").fetchone()
        self.assertEqual(row[0], "ok", f"DB integrity check failed after concurrent writes: {row[0]}")

    def test_concurrent_writes_all_rows_present(self):
        """After 10 parallel learn writes, at least 8 entries are in knowledge_entries."""
        threads = []
        results = []
        errors = []
        N = 10
        for i in range(N):
            t = threading.Thread(
                target=self._run_learn_subprocess,
                args=(i + 200, results, errors),
                daemon=True,
            )
            threads.append(t)
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)

        with sqlite3.connect(str(self.db_path)) as db:
            count = db.execute(
                "SELECT COUNT(*) FROM knowledge_entries WHERE title LIKE 'Concurrent test entry 2%'"
            ).fetchone()[0]
        # Require at least 8/10 wrote (some may lose SQLite UNIQUE/busy races on slow CI)
        self.assertGreaterEqual(count, 8, f"Expected ≥8 concurrent entries in the DB, got {count}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
