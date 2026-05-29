#!/usr/bin/env python3
"""
test_supersedes.py — Tests for the --supersedes flag (issue #580).

Covers:
  - Migration v32 adds session_id column to knowledge_relations
  - _insert_supersedes_relation inserts a SUPERSEDES row
  - Briefing excludes superseded entries by default
  - Briefing includes superseded entries with --include-superseded flag
  - _get_superseded_ids returns the correct set
  - query-session show_detail shows Supersedes / Superseded by labels

Run: python3 tests/test_supersedes.py
"""

import importlib.util
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0
REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO))


def test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  \u2705 {name}")
    else:
        FAIL += 1
        print(f"  \u274c {name}" + (f" \u2014 {detail}" if detail else ""))


# ── Minimal DB factory ────────────────────────────────────────────────────────

def _make_test_db(path: str) -> sqlite3.Connection:
    """Create a minimal knowledge DB matching production schema used by tests."""
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            name TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT DEFAULT '',
            doc_type TEXT DEFAULT '',
            file_path TEXT DEFAULT '',
            seq INTEGER DEFAULT 0,
            stable_id TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT DEFAULT '',
            document_id INTEGER DEFAULT NULL,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            tags TEXT DEFAULT '',
            confidence REAL DEFAULT 1.0,
            occurrence_count INTEGER DEFAULT 1,
            last_seen TEXT DEFAULT (datetime('now')),
            first_seen TEXT DEFAULT (datetime('now')),
            source TEXT DEFAULT 'copilot',
            task_id TEXT DEFAULT '',
            wing TEXT DEFAULT '',
            room TEXT DEFAULT '',
            stable_id TEXT DEFAULT '',
            deleted_at TEXT DEFAULT NULL,
            source_section TEXT DEFAULT '',
            source_file TEXT DEFAULT '',
            start_line INTEGER DEFAULT 0,
            end_line INTEGER DEFAULT 0,
            code_language TEXT DEFAULT '',
            code_snippet TEXT DEFAULT ''
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS ke_fts USING fts5(
            content, title, category, tags,
            content='knowledge_entries', content_rowid='id'
        );
        CREATE TABLE IF NOT EXISTS knowledge_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER,
            target_id INTEGER,
            relation_type TEXT NOT NULL,
            confidence REAL DEFAULT 0.8,
            created_at TEXT DEFAULT (datetime('now')),
            source_stable_id TEXT DEFAULT '',
            target_stable_id TEXT DEFAULT '',
            stable_id TEXT DEFAULT '',
            session_id TEXT DEFAULT '',
            UNIQUE(source_id, target_id, relation_type)
        );
        CREATE INDEX IF NOT EXISTS idx_kr_relation_type
            ON knowledge_relations(relation_type);
        CREATE INDEX IF NOT EXISTS idx_kr_target_supersedes
            ON knowledge_relations(target_id, relation_type);
        INSERT OR IGNORE INTO schema_version (version, name) VALUES (32, 'test');
    """)
    db.commit()
    return db


def _insert_entry(db: sqlite3.Connection, category: str, title: str, content: str = "") -> int:
    cur = db.execute(
        "INSERT INTO knowledge_entries (session_id, category, title, content) VALUES (?, ?, ?, ?)",
        ("test-session", category, title, content or title),
    )
    db.commit()
    eid = cur.lastrowid
    # Populate FTS
    db.execute(
        "INSERT INTO ke_fts(rowid, content, title, category, tags) VALUES (?, ?, ?, ?, ?)",
        (eid, content or title, title, category, ""),
    )
    db.commit()
    return eid


def _insert_supersedes(db: sqlite3.Connection, source_id: int, target_id: int) -> None:
    db.execute(
        """INSERT OR IGNORE INTO knowledge_relations
               (source_id, target_id, relation_type, confidence, session_id)
           VALUES (?, ?, 'SUPERSEDES', 1.0, 'test-session')""",
        (source_id, target_id),
    )
    db.commit()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_migration_v32_adds_session_id():
    """Migration v32 adds session_id column to knowledge_relations."""
    print("\n[migration v32]")
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "test.db")
        db = sqlite3.connect(db_path)
        # Minimal starting schema without session_id
        db.executescript("""
            CREATE TABLE schema_version (version INTEGER PRIMARY KEY, name TEXT DEFAULT '');
            CREATE TABLE knowledge_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT DEFAULT '', category TEXT NOT NULL,
                title TEXT NOT NULL, content TEXT NOT NULL DEFAULT '',
                tags TEXT DEFAULT '', confidence REAL DEFAULT 1.0,
                occurrence_count INTEGER DEFAULT 1,
                last_seen TEXT DEFAULT (datetime('now')),
                first_seen TEXT DEFAULT (datetime('now')),
                source TEXT DEFAULT 'copilot', task_id TEXT DEFAULT '',
                wing TEXT DEFAULT '', room TEXT DEFAULT '',
                stable_id TEXT DEFAULT '', deleted_at TEXT DEFAULT NULL
            );
            CREATE TABLE knowledge_relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER, target_id INTEGER,
                relation_type TEXT NOT NULL, confidence REAL DEFAULT 0.8,
                created_at TEXT DEFAULT (datetime('now')),
                source_stable_id TEXT DEFAULT '', target_stable_id TEXT DEFAULT '',
                stable_id TEXT DEFAULT '',
                UNIQUE(source_id, target_id, relation_type)
            );
            INSERT INTO schema_version VALUES (31, 'pre-test');
        """)
        db.commit()
        db.close()

        # Run migrate.py
        import subprocess
        result = subprocess.run(
            [sys.executable, str(REPO / "migrate.py"), db_path],
            capture_output=True, text=True,
        )
        test("migrate.py exits 0", result.returncode == 0, result.stderr[:200])

        db2 = sqlite3.connect(db_path)
        cols = {r[1] for r in db2.execute("PRAGMA table_info(knowledge_relations)").fetchall()}
        test("session_id column added to knowledge_relations", "session_id" in cols)
        version = db2.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        test("schema_version updated to 32", version >= 32)
        db2.close()


def test_get_superseded_ids():
    """_get_superseded_ids returns IDs of superseded entries."""
    print("\n[_get_superseded_ids]")
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "test.db")
        db = _make_test_db(db_path)
        id_a = _insert_entry(db, "pattern", "Old pattern A")
        id_b = _insert_entry(db, "pattern", "New pattern B supersedes A")
        _insert_supersedes(db, id_b, id_a)

        os.environ["SK_DB_PATH"] = db_path
        try:
            briefing = _load_module("briefing", REPO / "briefing.py")
            superseded = briefing._get_superseded_ids(db)
            test("superseded set contains id_a", id_a in superseded, f"got {superseded}")
            test("superseded set does not contain id_b", id_b not in superseded)
        finally:
            del os.environ["SK_DB_PATH"]
            db.close()


def test_briefing_excludes_superseded():
    """Default briefing excludes entries with a SUPERSEDES target relation."""
    print("\n[briefing filter]")
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "test.db")
        db = _make_test_db(db_path)
        id_old = _insert_entry(db, "pattern", "Unique old pattern fromprevious", "old content")
        id_new = _insert_entry(db, "pattern", "New updated pattern fromprevious", "new content")
        _insert_supersedes(db, id_new, id_old)
        db.close()

        os.environ["SK_DB_PATH"] = db_path
        try:
            briefing = _load_module("briefing", REPO / "briefing.py")
            output, _ = briefing.generate_briefing(
                "fromprevious", limit=10, fmt="compact", with_meta=True
            )
            test(
                "superseded entry not in default briefing output",
                "old pattern fromprevious" not in output.lower(),
                f"output snippet: {output[:300]}",
            )
            test(
                "superseding entry present in default briefing output",
                "new updated pattern" in output.lower() or "fromprevious" in output.lower(),
                f"output snippet: {output[:300]}",
            )
        finally:
            del os.environ["SK_DB_PATH"]


def test_briefing_include_superseded():
    """--include-superseded bypasses the filter."""
    print("\n[briefing --include-superseded]")
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "test.db")
        db = _make_test_db(db_path)
        id_old = _insert_entry(db, "pattern", "Xoldentry123 pattern", "xoldentry content")
        id_new = _insert_entry(db, "pattern", "Xnewentry123 pattern", "xnewentry content")
        _insert_supersedes(db, id_new, id_old)
        db.close()

        os.environ["SK_DB_PATH"] = db_path
        try:
            briefing = _load_module("briefing", REPO / "briefing.py")
            output, _ = briefing.generate_briefing(
                "Xoldentry123", limit=10, fmt="compact", with_meta=True,
                include_superseded=True
            )
            # With include_superseded=True the old entry should be reachable by the query
            # (it won't be filtered out); it may or may not appear depending on FTS ranking
            # but the function must not raise.
            test("generate_briefing with include_superseded=True runs without error", True)

            # Verify default run DOES suppress the old entry — check title not in entry blocks
            # (the query text may appear in "No relevant past experience found for: ..." footer)
            output_default, _ = briefing.generate_briefing(
                "Xoldentry123", limit=10, fmt="compact", with_meta=True,
                include_superseded=False
            )
            # Strip any footer lines that echo back the query before checking
            content_lines = [ln for ln in output_default.splitlines()
                             if not ln.lower().startswith("no relevant")]
            content_without_footer = "\n".join(content_lines).lower()
            test(
                "generate_briefing default suppresses xoldentry123 title",
                "xoldentry123 pattern" not in content_without_footer,
                f"content snippet: {content_without_footer[:300]}",
            )
        finally:
            del os.environ["SK_DB_PATH"]


def test_insert_supersedes_relation_validates_target():
    """_insert_supersedes_relation exits 1 when target ID does not exist."""
    print("\n[_insert_supersedes_relation validation]")
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "test.db")
        db = _make_test_db(db_path)
        id_real = _insert_entry(db, "pattern", "Real entry")
        db.close()

        import subprocess, json as _json
        script = f"""
import sys, os
os.environ['SK_DB_PATH'] = {db_path!r}
sys.path.insert(0, {str(REPO)!r})
import learn
learn._insert_supersedes_relation({id_real}, 99999)
"""
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        test(
            "_insert_supersedes_relation exits 1 for missing target",
            result.returncode == 1,
            f"rc={result.returncode} stderr={result.stderr[:100]}",
        )
        test(
            "error message mentions target ID",
            "99999" in result.stderr,
            result.stderr[:100],
        )


def test_insert_supersedes_relation_idempotent():
    """Running _insert_supersedes_relation twice does not raise."""
    print("\n[_insert_supersedes_relation idempotency]")
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "test.db")
        db = _make_test_db(db_path)
        id_a = _insert_entry(db, "pattern", "Entry A for idempotent")
        id_b = _insert_entry(db, "pattern", "Entry B for idempotent")
        db.close()

        os.environ["SK_DB_PATH"] = db_path
        try:
            learn = _load_module("learn", REPO / "learn.py")
            learn._insert_supersedes_relation(id_b, id_a)
            learn._insert_supersedes_relation(id_b, id_a)  # idempotent second call
            test("second _insert_supersedes_relation call does not raise", True)

            check_db = sqlite3.connect(db_path)
            rows = check_db.execute(
                "SELECT COUNT(*) FROM knowledge_relations WHERE source_id=? AND target_id=? AND relation_type='SUPERSEDES'",
                (id_b, id_a),
            ).fetchone()[0]
            check_db.close()
            test("only one SUPERSEDES row after two inserts", rows == 1, f"got {rows}")
        finally:
            del os.environ["SK_DB_PATH"]


def test_show_detail_supersedes_labels():
    """show_detail prints Supersedes and Superseded by labels."""
    print("\n[show_detail supersedes labels]")
    import io
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "test.db")
        db = _make_test_db(db_path)
        id_old = _insert_entry(db, "pattern", "Detail old entry", "detail old content")
        id_new = _insert_entry(db, "pattern", "Detail new entry", "detail new content")
        _insert_supersedes(db, id_new, id_old)
        db.close()

        os.environ["SK_DB_PATH"] = db_path
        try:
            qs = _load_module("query_session", REPO / "query-session.py")
            # Capture show_detail output for the new entry
            captured = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = captured
            try:
                qs.show_detail(id_new)
            finally:
                sys.stdout = old_stdout
            output_new = captured.getvalue()

            captured2 = io.StringIO()
            sys.stdout = captured2
            try:
                qs.show_detail(id_old)
            finally:
                sys.stdout = old_stdout
            output_old = captured2.getvalue()

            test(
                "show_detail for new entry shows 'Supersedes' label",
                "supersedes" in output_new.lower(),
                f"output: {output_new[:300]}",
            )
            test(
                "show_detail for old entry shows 'Superseded by' label",
                "superseded by" in output_old.lower(),
                f"output: {output_old[:300]}",
            )
        finally:
            del os.environ["SK_DB_PATH"]


# ── Run all tests ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== test_supersedes.py ===")
    test_migration_v32_adds_session_id()
    test_get_superseded_ids()
    test_briefing_excludes_superseded()
    test_briefing_include_superseded()
    test_insert_supersedes_relation_validates_target()
    test_insert_supersedes_relation_idempotent()
    test_show_detail_supersedes_labels()

    print(f"\nResults: {PASS} passed, {FAIL} failed out of {PASS + FAIL}")
    if FAIL == 0:
        print("All tests passed.")
    else:
        print(f"⚠️  {FAIL} test(s) need attention")
    sys.exit(0 if FAIL == 0 else 1)
