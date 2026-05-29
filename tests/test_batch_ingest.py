#!/usr/bin/env python3
"""tests/test_batch_ingest.py — Tests for batch ingest functionality (issue #576).

Covers:
  - _parse_markdown_sections extracts headings and maps categories correctly
  - _batch_stable_id produces deterministic 12-char IDs
  - batch_ingest_sections inserts 3+ rows from fixture file
  - Re-ingest is idempotent (same row count, skipped==total on second run)
  - --dry-run prints table but does not insert

Run: python3 tests/test_batch_ingest.py
"""

import importlib.util
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0
REPO = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures" / "checkpoints"

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


def _load_learn():
    """Load learn.py as a module (avoids sys.modules pollution between test runs)."""
    spec = importlib.util.spec_from_file_location("learn_mod", REPO / "learn.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_test_db(tmp_dir: Path) -> Path:
    """Create a minimal knowledge.db for tests."""
    db_path = tmp_dir / "knowledge.db"
    db = sqlite3.connect(str(db_path))
    db.execute("""
        CREATE TABLE knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            stable_id TEXT,
            content TEXT,
            tags TEXT DEFAULT '',
            confidence REAL DEFAULT 0.7,
            session_id TEXT,
            occurrence_count INTEGER DEFAULT 1,
            first_seen TEXT,
            last_seen TEXT,
            wing TEXT DEFAULT '',
            room TEXT DEFAULT '',
            facts TEXT DEFAULT '[]',
            est_tokens INTEGER DEFAULT 0,
            task_id TEXT DEFAULT '',
            affected_files TEXT DEFAULT '[]'
        )
    """)
    db.execute("""
        CREATE TABLE ke_fts (
            rowid INTEGER,
            title TEXT,
            content TEXT,
            tags TEXT,
            category TEXT,
            wing TEXT,
            room TEXT,
            facts TEXT
        )
    """)
    db.execute("""
        CREATE TABLE sync_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    db.commit()
    db.close()
    return db_path


# ── Unit tests ────────────────────────────────────────────────────────────────

def test_parse_markdown_sections():
    """_parse_markdown_sections extracts headings and maps categories."""
    learn = _load_learn()
    text = """# Top-level heading (ignored)

Some preamble text.

## Decision

We decided to use SQLite for local storage because it is zero-dependency.

## Pattern

Always use parameterized SQL queries to prevent injection.

## Mistake

Forgot to handle SQLITE_BUSY errors in the retry loop.
"""
    sections = learn._parse_markdown_sections(text)
    test("parse: finds 3 sections", len(sections) == 3, f"got {len(sections)}")
    test("parse: first heading is Decision", sections[0]["heading"] == "Decision")
    test("parse: Decision maps to decision", sections[0]["category"] == "decision")
    test("parse: Pattern maps to pattern", sections[1]["category"] == "pattern")
    test("parse: Mistake maps to mistake", sections[2]["category"] == "mistake")
    test("parse: all sections have content", all(s["content"] for s in sections))


def test_parse_skips_empty_sections():
    """_parse_markdown_sections skips sections with no content."""
    learn = _load_learn()
    text = """## Empty Section

## Non-empty Section

This has content.
"""
    sections = learn._parse_markdown_sections(text)
    test("parse_empty: only non-empty sections returned", len(sections) == 1, f"got {len(sections)}")
    test("parse_empty: correct heading", sections[0]["heading"] == "Non-empty Section")


def test_batch_stable_id_properties():
    """_batch_stable_id is deterministic, 12 chars, and unique per (source, heading)."""
    learn = _load_learn()
    sid1 = learn._batch_stable_id("path/to/file.md", "Decision")
    sid2 = learn._batch_stable_id("path/to/file.md", "Decision")
    sid3 = learn._batch_stable_id("path/to/file.md", "Pattern")
    sid4 = learn._batch_stable_id("other/file.md", "Decision")

    test("stable_id: deterministic", sid1 == sid2, f"{sid1!r} != {sid2!r}")
    test("stable_id: different headings differ", sid1 != sid3, f"both={sid1!r}")
    test("stable_id: different sources differ", sid1 != sid4, f"both={sid1!r}")
    test("stable_id: length is 12", len(sid1) == 12, f"len={len(sid1)}")
    test("stable_id: hex chars only", all(c in "0123456789abcdef" for c in sid1))


def test_heading_to_category_mapping():
    """_heading_to_category maps known headings and falls back to discovery."""
    learn = _load_learn()
    mappings = [
        ("Decision", "decision"),
        ("Decisions", "decision"),
        ("Pattern", "pattern"),
        ("Mistake", "mistake"),
        ("Technical Details", "discovery"),
        ("Next Steps", "discovery"),
        ("Feature", "feature"),
        ("Tool", "tool"),
        ("Unknown Heading", "discovery"),
        ("Custom Section", "discovery"),
    ]
    for heading, expected in mappings:
        got = learn._heading_to_category(heading)
        test(f"category map: '{heading}' → '{expected}'", got == expected, f"got '{got}'")


# ── Integration tests (require test DB) ───────────────────────────────────────

def test_from_checkpoint_inserts_rows():
    """batch_ingest_sections inserts rows from the fixture file."""
    sample = FIXTURES / "sample.md"
    test("fixture: sample.md exists", sample.exists(), str(sample))
    if not sample.exists():
        return

    learn = _load_learn()
    text = sample.read_text(encoding="utf-8")
    sections = learn._parse_markdown_sections(text)

    with tempfile.TemporaryDirectory() as tmp:
        db_path = _make_test_db(Path(tmp))
        orig_db = learn.DB_PATH
        learn.DB_PATH = db_path
        try:
            result = learn.batch_ingest_sections(
                sections,
                str(sample.resolve()),
                dry_run=False,
            )
            db = sqlite3.connect(str(db_path))
            count = db.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
            db.close()

            test("from_checkpoint: inserts ≥3 rows", count >= 3, f"got {count}")
            test("from_checkpoint: inserted==DB count", result["inserted"] == count,
                 f"result={result['inserted']} DB={count}")
            test("from_checkpoint: no skipped on first run", result["skipped"] == 0,
                 f"skipped={result['skipped']}")
            test("from_checkpoint: dry_run=False", result["dry_run"] is False)
        finally:
            learn.DB_PATH = orig_db


def test_idempotent_reingest():
    """Re-ingesting the same checkpoint skips all existing entries."""
    sample = FIXTURES / "sample.md"
    if not sample.exists():
        test("idempotency: skipped (no fixture)", False, "fixture missing")
        return

    learn = _load_learn()
    text = sample.read_text(encoding="utf-8")
    sections = learn._parse_markdown_sections(text)
    source_key = str(sample.resolve())

    with tempfile.TemporaryDirectory() as tmp:
        db_path = _make_test_db(Path(tmp))
        orig_db = learn.DB_PATH
        learn.DB_PATH = db_path
        try:
            # First run — insert all
            r1 = learn.batch_ingest_sections(sections, source_key, dry_run=False)
            db = sqlite3.connect(str(db_path))
            count1 = db.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
            db.close()

            # Second run — should skip all
            r2 = learn.batch_ingest_sections(sections, source_key, dry_run=False)
            db = sqlite3.connect(str(db_path))
            count2 = db.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
            db.close()

            test("idempotency: row count unchanged", count1 == count2,
                 f"first={count1} second={count2}")
            test("idempotency: second run inserts 0", r2["inserted"] == 0,
                 f"got {r2['inserted']}")
            test("idempotency: second run skips all", r2["skipped"] == len(sections),
                 f"skipped={r2['skipped']} expected={len(sections)}")
        finally:
            learn.DB_PATH = orig_db


def test_dry_run_no_insert():
    """dry_run=True prints table but does not insert any rows."""
    sample = FIXTURES / "sample.md"
    if not sample.exists():
        test("dry_run: skipped (no fixture)", False, "fixture missing")
        return

    learn = _load_learn()
    text = sample.read_text(encoding="utf-8")
    sections = learn._parse_markdown_sections(text)
    source_key = str(sample.resolve())

    with tempfile.TemporaryDirectory() as tmp:
        db_path = _make_test_db(Path(tmp))
        orig_db = learn.DB_PATH
        learn.DB_PATH = db_path
        try:
            result = learn.batch_ingest_sections(sections, source_key, dry_run=True)
            db = sqlite3.connect(str(db_path))
            count = db.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
            db.close()

            test("dry_run: no rows inserted", count == 0, f"got {count}")
            test("dry_run: result dry_run=True", result.get("dry_run") is True)
            test("dry_run: total matches section count", result["total"] == len(sections),
                 f"total={result['total']} sections={len(sections)}")
            test("dry_run: inserted=0", result["inserted"] == 0, f"got {result['inserted']}")
            test("dry_run: rows list populated", len(result.get("rows", [])) == len(sections))
        finally:
            learn.DB_PATH = orig_db


def test_stable_id_overwrite_after_insert():
    """After insert, stable_id is updated to the batch-derived value."""
    learn = _load_learn()
    sections = [{"heading": "Test Decision", "content": "Use X over Y.", "category": "decision"}]
    source_key = "test-source-576"

    with tempfile.TemporaryDirectory() as tmp:
        db_path = _make_test_db(Path(tmp))
        orig_db = learn.DB_PATH
        learn.DB_PATH = db_path
        try:
            result = learn.batch_ingest_sections(sections, source_key, dry_run=False)
            expected_sid = learn._batch_stable_id(source_key, "Test Decision")
            db = sqlite3.connect(str(db_path))
            row = db.execute("SELECT stable_id FROM knowledge_entries LIMIT 1").fetchone()
            db.close()

            test("stable_id overwrite: entry inserted", result["inserted"] == 1)
            test("stable_id overwrite: stable_id is batch value",
                 row is not None and row[0] == expected_sid,
                 f"got {row[0] if row else 'None'}")
        finally:
            learn.DB_PATH = orig_db


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\ntest_batch_ingest.py")
    print("=" * 60)

    print("\n[1] _parse_markdown_sections")
    test_parse_markdown_sections()

    print("\n[2] _parse_markdown_sections — empty sections")
    test_parse_skips_empty_sections()

    print("\n[3] _batch_stable_id properties")
    test_batch_stable_id_properties()

    print("\n[4] _heading_to_category mapping")
    test_heading_to_category_mapping()

    print("\n[5] --from-checkpoint inserts rows")
    test_from_checkpoint_inserts_rows()

    print("\n[6] Idempotent re-ingest")
    test_idempotent_reingest()

    print("\n[7] --dry-run does not insert")
    test_dry_run_no_insert()

    print("\n[8] stable_id overwrite after insert")
    test_stable_id_overwrite_after_insert()

    print(f"\nResults: {PASS} passed, {FAIL} failed")
    if FAIL:
        sys.exit(1)
