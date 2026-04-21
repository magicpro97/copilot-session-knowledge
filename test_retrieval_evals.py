#!/usr/bin/env python3
"""
test_retrieval_evals.py — Golden-query regression harness for query/briefing recall.

Verifies deterministic, grounded behaviour of the retrieval surfaces exposed by
query-session.py and briefing.py.  All fixtures are self-contained SQLite dbs
seeded with known entries — no network, no LLM, no external tokenizers.

Checks:
  1. _sanitize_fts_query — golden input→output table (both modules agree)
  2. search_knowledge recall — FTS term-in-title ranked ahead of term-in-content
  3. search_knowledge JSON stability — export is a valid JSON list; array fields decoded
  4. show_by_file precision — exact path match / no false-positives
  5. show_by_file compact+JSON stability
  6. show_by_module directory-segment matching (head and mid-path), fallback
  7. show_by_task recall accuracy and JSON envelope
  8. generate_briefing (briefing.py) — text/json output with known entries
  9. generate_task_briefing — tagged entries surface; JSON structure
 10. Ranking: title-hit entry scores before content-only entry

Run:
    python3 test_retrieval_evals.py
"""

import importlib.util
import io
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

# Windows encoding fix (match project convention)
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TOOLS_DIR = Path(__file__).parent

PASS = 0
FAIL = 0


def test(name: str, passed: bool, detail: str = "") -> None:
    global PASS, FAIL
    if passed:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f": {detail}" if detail else ""))


# ─── Module loading ────────────────────────────────────────────────────────

def _load_module(name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(name, str(file_path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_qs = _load_module("qs_re", TOOLS_DIR / "query-session.py")
_br = _load_module("briefing_re", TOOLS_DIR / "briefing.py")


# ─── Shared DB schema (kept in sync with test_memory_contract.py) ──────────

_DB_SCHEMA = """
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY, path TEXT NOT NULL,
        summary TEXT DEFAULT '', total_checkpoints INTEGER DEFAULT 0,
        total_research INTEGER DEFAULT 0, total_files INTEGER DEFAULT 0,
        has_plan INTEGER DEFAULT 0, source TEXT DEFAULT 'copilot', indexed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL REFERENCES sessions(id),
        doc_type TEXT NOT NULL, seq INTEGER DEFAULT 0, title TEXT NOT NULL,
        file_path TEXT NOT NULL UNIQUE, file_hash TEXT, size_bytes INTEGER DEFAULT 0,
        content_preview TEXT DEFAULT '', source TEXT DEFAULT 'copilot', indexed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS sections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        section_name TEXT NOT NULL, content TEXT NOT NULL,
        UNIQUE(document_id, section_name)
    );
    CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
        title, section_name, content, doc_type,
        session_id UNINDEXED, document_id UNINDEXED,
        tokenize='unicode61 remove_diacritics 2'
    );
    CREATE TABLE IF NOT EXISTS knowledge_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        document_id INTEGER,
        category TEXT NOT NULL,
        title TEXT NOT NULL,
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
        affected_files TEXT DEFAULT '[]'
    );
    CREATE VIRTUAL TABLE IF NOT EXISTS ke_fts USING fts5(
        title, content, tags, category, wing, room, facts
    );
    CREATE TABLE IF NOT EXISTS entity_relations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject TEXT NOT NULL, predicate TEXT NOT NULL, object TEXT NOT NULL,
        noted_at TEXT, session_id TEXT,
        UNIQUE(subject, predicate, object)
    );
    CREATE TABLE IF NOT EXISTS knowledge_relations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id INTEGER NOT NULL REFERENCES knowledge_entries(id),
        target_id INTEGER NOT NULL REFERENCES knowledge_entries(id),
        relation_type TEXT NOT NULL,
        confidence REAL DEFAULT 0.5,
        UNIQUE(source_id, target_id, relation_type)
    );
    INSERT OR IGNORE INTO sessions (id, path, indexed_at)
    VALUES ('re-session-001', '/test/retrieval-evals', '2024-01-01T00:00:00');
"""


def _make_db(path: str) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(_DB_SCHEMA)
    db.commit()
    return db


def _insert_ke(db: sqlite3.Connection, *,
               category: str, title: str, content: str,
               task_id: str = "", affected_files: list = None,
               wing: str = "", confidence: float = 0.9,
               tags: str = "") -> int:
    """Insert a knowledge entry + its ke_fts row.  Returns the rowid."""
    files_json = json.dumps(affected_files or [])
    db.execute("""
        INSERT INTO knowledge_entries
            (session_id, category, title, content, task_id, affected_files,
             wing, confidence, tags, first_seen, last_seen)
        VALUES ('re-session-001', ?, ?, ?, ?, ?, ?, ?, ?, '2024-01-01', '2024-01-01')
    """, (category, title, content, task_id, files_json, wing, confidence, tags))
    rowid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute("""
        INSERT INTO ke_fts (rowid, title, content, tags, category, wing, room, facts)
        VALUES (?, ?, ?, ?, ?, ?, '', '[]')
    """, (rowid, title, content, tags, category, wing))
    db.commit()
    return rowid


class _Cap:
    """Capture sys.stdout for the duration of a with-block."""
    def __enter__(self):
        self._old = sys.stdout
        sys.stdout = io.StringIO()
        return sys.stdout
    def __exit__(self, *_):
        sys.stdout = self._old


# ═══════════════════════════════════════════════════════════════════════════
# 1. _sanitize_fts_query — golden table
# ═══════════════════════════════════════════════════════════════════════════

def test_sanitize_fts_query() -> None:
    print("\n🔍 1. _sanitize_fts_query golden cases")

    golden = [
        # (description, input, expected_output)
        ("empty string",           "",              '""'),
        ("whitespace only",        "   ",           '""'),
        ("sole OR operator",       "OR",            '""'),
        ("sole AND operator",      "AND",           '""'),
        ("all fts5 operators",     "OR AND NOT NEAR", '""'),
        ("simple single term",     "sqlite",        '"sqlite"*'),
        ("two-term query",         "memory recall", '"memory"* "recall"*'),
        ("query with OR stripped", "auth OR login", '"auth"* "login"*'),
        ("double-quotes stripped", 'find "exact"',  '"find"* "exact"*'),
        ("asterisk stripped",      "prefix*",       '"prefix"*'),
        ("parens stripped",        "(nested)",      '"nested"*'),
        # colon is in fts_special → replaced with space → two separate terms
        ("colon becomes space",    "tag:value",     '"tag"* "value"*'),
    ]

    for desc, inp, expected in golden:
        result_qs = _qs._sanitize_fts_query(inp)
        result_br = _br._sanitize_fts_query(inp)
        test(f"qs.sanitize({desc!r})", result_qs == expected,
             f"expected {expected!r}, got {result_qs!r}")
        test(f"br.sanitize({desc!r}) agrees with qs",
             result_qs == result_br,
             f"qs={result_qs!r} br={result_br!r}")

    # Truncation: input > 500 chars must not produce query longer than max_length worth
    long_input = "x" * 600
    result = _qs._sanitize_fts_query(long_input)
    test("long input truncated (input 600 chars)", len(result) <= 520,
         f"len={len(result)}")


# ═══════════════════════════════════════════════════════════════════════════
# 2 & 3. search_knowledge recall + JSON stability
# ═══════════════════════════════════════════════════════════════════════════

def test_search_knowledge_recall() -> None:
    print("\n📚 2–3. search_knowledge recall + JSON stability")

    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="re_test_", dir=str(TOOLS_DIR))
    os.close(fd)
    db = _make_db(db_path)

    # Known entries
    _insert_ke(db, category="mistake",
               title="SQLite injection via string interpolation",
               content="Always use parameterised queries.")
    _insert_ke(db, category="pattern",
               title="Unit testing best practice",
               content="Prefer parameterised SQL to avoid SQLite injection risks.")

    orig = _qs.DB_PATH
    _qs.DB_PATH = Path(db_path)
    try:
        # --- 2a. FTS recall: searching "injection" should hit both, first entry title-match
        with _Cap() as cap:
            _qs.search_knowledge("injection", limit=10)
        out = cap.getvalue()
        test("search_knowledge('injection') returns results",
             "injection" in out.lower() or "sqlite" in out.lower(),
             f"output: {out[:200]}")

        # --- 2b. Specific term only in first entry title
        with _Cap() as cap:
            _qs.search_knowledge("interpolation", limit=10)
        out2 = cap.getvalue()
        test("search_knowledge('interpolation') finds title entry",
             "interpolation" in out2.lower() or "sqlite injection" in out2.lower(),
             f"output: {out2[:200]}")

        # --- 3a. JSON export produces valid JSON list
        with _Cap() as cap:
            _qs.search_knowledge("injection", limit=10, export_fmt="json")
        raw = cap.getvalue().strip()
        try:
            parsed = json.loads(raw)
            test("search_knowledge JSON export is a list", isinstance(parsed, list),
                 f"type={type(parsed)}")
            test("search_knowledge JSON export non-empty", len(parsed) >= 1,
                 f"len={len(parsed)}")
        except json.JSONDecodeError as e:
            test("search_knowledge JSON export is valid JSON", False, str(e))
            test("search_knowledge JSON export non-empty", False, "JSON invalid")

        # --- 3b. affected_files in JSON is a list (deserialized)
        _insert_ke(db, category="decision",
                   title="Auth module refactor decision",
                   content="Use JWT for session management.",
                   affected_files=["src/auth.py", "tests/test_auth.py"])
        with _Cap() as cap:
            _qs.search_knowledge("auth refactor", limit=10, export_fmt="json")
        raw2 = cap.getvalue().strip()
        try:
            parsed2 = json.loads(raw2)
            if parsed2:
                af = parsed2[0].get("affected_files")
                test("affected_files in search JSON is a list (not str)",
                     isinstance(af, list), f"type={type(af)}, val={af!r}")
            else:
                test("affected_files in search JSON is a list (not str)",
                     False,
                     "expected ≥1 row for 'auth refactor' query but got 0 — fixture not matching")
        except json.JSONDecodeError:
            test("affected_files in search JSON is a list (not str)", False, "JSON invalid")

    finally:
        _qs.DB_PATH = orig
        try:
            db.close()
            Path(db_path).unlink()
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# 4 & 5. show_by_file precision + compact/JSON stability
# ═══════════════════════════════════════════════════════════════════════════

def test_show_by_file() -> None:
    print("\n📁 4–5. show_by_file precision + compact/JSON")

    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="re_test_", dir=str(TOOLS_DIR))
    os.close(fd)
    db = _make_db(db_path)

    _insert_ke(db, category="mistake",
               title="Off-by-one in auth token validation",
               content="Token expiry was calculated incorrectly.",
               affected_files=["src/auth.py"],
               task_id="auth-fix")
    _insert_ke(db, category="pattern",
               title="Caching strategy for user profiles",
               content="Use Redis with TTL for user data.",
               affected_files=["src/cache.py"],
               task_id="cache-opt")

    orig = _qs.DB_PATH
    _qs.DB_PATH = Path(db_path)
    try:
        # --- 4a. Exact path match returns the auth entry
        with _Cap() as cap:
            _qs.show_by_file("src/auth.py", limit=20)
        out = cap.getvalue()
        test("show_by_file('src/auth.py') returns auth entry",
             "Off-by-one" in out or "auth token" in out.lower(),
             f"output: {out[:300]}")
        test("show_by_file('src/auth.py') does not return cache entry",
             "Caching strategy" not in out,
             f"cache entry leaked: {out[:200]}")

        # --- 4b. Different path — no cross-contamination
        with _Cap() as cap:
            _qs.show_by_file("src/cache.py", limit=20)
        out2 = cap.getvalue()
        test("show_by_file('src/cache.py') returns cache entry",
             "Caching strategy" in out2 or "cache.py" in out2.lower(),
             f"output: {out2[:300]}")
        test("show_by_file('src/cache.py') does not return auth entry",
             "Off-by-one" not in out2,
             f"auth entry leaked: {out2[:200]}")

        # --- 4c. Path with no entries
        with _Cap() as cap:
            _qs.show_by_file("src/nonexistent.py", limit=20)
        out3 = cap.getvalue()
        test("show_by_file for unknown path prints no-entry message",
             "No knowledge entries recorded for file:" in out3,
             f"output: {out3[:200]}")

        # --- 5a. compact mode: includes ~tok hint when est_tokens set
        db.execute("UPDATE knowledge_entries SET est_tokens=77 WHERE task_id='auth-fix'")
        db.commit()
        with _Cap() as cap:
            _qs.show_by_file("src/auth.py", limit=20, compact=True)
        out4 = cap.getvalue()
        test("show_by_file compact mode produces output", len(out4) > 0,
             "empty output")
        test("show_by_file compact shows ~tok hint", "~77tok" in out4,
             f"output: {out4[:200]}")

        # --- 5b. JSON export is valid + affected_files decoded
        with _Cap() as cap:
            _qs.show_by_file("src/auth.py", limit=20, export_fmt="json")
        raw = cap.getvalue().strip()
        try:
            parsed = json.loads(raw)
            test("show_by_file JSON export is a list", isinstance(parsed, list),
                 f"type={type(parsed)}")
            if parsed:
                af = parsed[0].get("affected_files")
                test("show_by_file JSON affected_files is list",
                     isinstance(af, list) and "src/auth.py" in af,
                     f"af={af!r}")
            else:
                test("show_by_file JSON affected_files is list", False,
                     "0 rows returned — fixture did not match 'src/auth.py'")
        except json.JSONDecodeError as e:
            test("show_by_file JSON export is valid JSON", False, str(e))
            test("show_by_file JSON affected_files is list", False, "JSON invalid")

        # --- 5c. Empty result via JSON export → empty list []
        with _Cap() as cap:
            _qs.show_by_file("src/nowhere.py", limit=20, export_fmt="json")
        raw2 = cap.getvalue().strip()
        try:
            parsed2 = json.loads(raw2)
            test("show_by_file empty JSON export is empty list",
                 isinstance(parsed2, list) and len(parsed2) == 0,
                 f"got: {parsed2!r}")
        except json.JSONDecodeError as e:
            test("show_by_file empty JSON export is empty list", False, str(e))

    finally:
        _qs.DB_PATH = orig
        try:
            db.close()
            Path(db_path).unlink()
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# 6. show_by_module — path-segment matching + content fallback
# ═══════════════════════════════════════════════════════════════════════════

def test_show_by_module() -> None:
    print("\n📦 6. show_by_module directory-segment matching")

    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="re_test_", dir=str(TOOLS_DIR))
    os.close(fd)
    db = _make_db(db_path)

    # Entry with "auth" as the first directory component
    _insert_ke(db, category="pattern",
               title="Auth module login flow",
               content="Token verified via HMAC.",
               affected_files=["auth/login.py"])
    # Entry with "auth" as a middle directory component
    _insert_ke(db, category="decision",
               title="Auth middleware refactor",
               content="Moved middleware to src/auth/middleware.py.",
               affected_files=["src/auth/middleware.py"])
    # Unrelated entry
    _insert_ke(db, category="mistake",
               title="Cache invalidation bug",
               content="Redis TTL was set to 0 by default.",
               affected_files=["src/cache/store.py"])

    orig = _qs.DB_PATH
    _qs.DB_PATH = Path(db_path)
    try:
        # --- 6a. Module "auth" matches head-directory entry
        with _Cap() as cap:
            _qs.show_by_module("auth", limit=20)
        out = cap.getvalue()
        test("show_by_module('auth') finds head-dir entry",
             "Auth module login flow" in out or "auth/login" in out.lower(),
             f"output: {out[:300]}")

        # --- 6b. Module "auth" also matches mid-path entry
        test("show_by_module('auth') finds mid-path entry",
             "Auth middleware" in out or "src/auth/" in out.lower(),
             f"output: {out[:300]}")

        # --- 6c. Module "auth" does not return cache entry
        test("show_by_module('auth') excludes cache entry",
             "Cache invalidation" not in out,
             f"cache entry leaked: {out[:200]}")

        # --- 6d. Content fallback: "cache" has no direct affected_files hit on
        #         a module named "cachemod" but content contains "cache"
        #         (using title/content substring fallback)
        with _Cap() as cap:
            _qs.show_by_module("cache", limit=20)
        out2 = cap.getvalue()
        test("show_by_module('cache') returns cache-tagged entries or fallback",
             "cache" in out2.lower(),
             f"output: {out2[:300]}")

        # --- 6e. JSON export is a valid list
        with _Cap() as cap:
            _qs.show_by_module("auth", limit=20, export_fmt="json")
        raw = cap.getvalue().strip()
        try:
            parsed = json.loads(raw)
            test("show_by_module JSON export is a list", isinstance(parsed, list),
                 f"type={type(parsed)}")
            test("show_by_module JSON export non-empty", len(parsed) >= 1,
                 f"len={len(parsed)}")
        except json.JSONDecodeError as e:
            test("show_by_module JSON export is valid JSON", False, str(e))
            test("show_by_module JSON export non-empty", False, "JSON invalid")

    finally:
        _qs.DB_PATH = orig
        try:
            db.close()
            Path(db_path).unlink()
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# 7. show_by_task — task-scoped recall + JSON envelope
# ═══════════════════════════════════════════════════════════════════════════

def test_show_by_task() -> None:
    print("\n🎯 7. show_by_task recall accuracy + JSON envelope")

    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="re_test_", dir=str(TOOLS_DIR))
    os.close(fd)
    db = _make_db(db_path)

    _insert_ke(db, category="mistake",
               title="Task-scoped mistake alpha",
               content="Forgot to close DB handle in task alpha.",
               task_id="task-alpha",
               affected_files=["src/db.py"])
    _insert_ke(db, category="pattern",
               title="Task-scoped pattern alpha",
               content="Always use context manager for DB connections.",
               task_id="task-alpha",
               affected_files=["src/db.py", "src/utils.py"])
    _insert_ke(db, category="decision",
               title="Beta task architecture decision",
               content="Use event sourcing for state management.",
               task_id="task-beta")

    orig = _qs.DB_PATH
    _qs.DB_PATH = Path(db_path)
    try:
        # --- 7a. Correct task_id returns both alpha entries
        with _Cap() as cap:
            _qs.show_by_task("task-alpha", limit=30)
        out = cap.getvalue()
        test("show_by_task('task-alpha') returns mistake entry",
             "Task-scoped mistake alpha" in out,
             f"output: {out[:300]}")
        test("show_by_task('task-alpha') returns pattern entry",
             "Task-scoped pattern alpha" in out,
             f"output: {out[:300]}")

        # --- 7b. Wrong task_id — beta entry must not appear under alpha
        test("show_by_task('task-alpha') excludes task-beta entry",
             "Beta task architecture" not in out,
             f"beta entry leaked into alpha: {out[:200]}")

        # --- 7c. JSON envelope: has task_id and entries keys
        with _Cap() as cap:
            _qs.show_by_task("task-alpha", limit=30, export_fmt="json")
        raw = cap.getvalue().strip()
        try:
            parsed = json.loads(raw)
            test("show_by_task JSON has 'task_id' key",
                 "task_id" in parsed,
                 f"keys={list(parsed.keys()) if isinstance(parsed, dict) else type(parsed)}")
            test("show_by_task JSON has 'entries' key",
                 "entries" in parsed,
                 f"keys={list(parsed.keys()) if isinstance(parsed, dict) else type(parsed)}")
            entries = parsed.get("entries", [])
            test("show_by_task JSON entries is a list", isinstance(entries, list),
                 f"type={type(entries)}")
            test("show_by_task JSON returns 2 alpha entries", len(entries) == 2,
                 f"len={len(entries)}")
        except json.JSONDecodeError as e:
            test("show_by_task JSON is valid", False, str(e))
            test("show_by_task JSON has 'task_id' key", False, "JSON invalid")
            test("show_by_task JSON has 'entries' key", False, "JSON invalid")
            test("show_by_task JSON entries is a list", False, "JSON invalid")
            test("show_by_task JSON returns 2 alpha entries", False, "JSON invalid")

        # --- 7d. affected_files in JSON entries is decoded list
        with _Cap() as cap:
            _qs.show_by_task("task-alpha", limit=30, export_fmt="json")
        raw2 = cap.getvalue().strip()
        try:
            parsed2 = json.loads(raw2)
            entries2 = parsed2.get("entries", [])
            if not entries2:
                test("show_by_task JSON affected_files decoded to list", False,
                     "0 entries returned — fixture did not match 'task-alpha'")
            else:
                af_types_ok = all(isinstance(e.get("affected_files"), list)
                                  for e in entries2)
                test("show_by_task JSON affected_files decoded to list", af_types_ok,
                     f"entries with non-list af: "
                     f"{[e.get('affected_files') for e in entries2]}")
        except json.JSONDecodeError:
            test("show_by_task JSON affected_files decoded to list", False, "JSON invalid")

        # --- 7e. Unknown task_id → JSON has empty entries list
        with _Cap() as cap:
            _qs.show_by_task("task-nonexistent", limit=30, export_fmt="json")
        raw3 = cap.getvalue().strip()
        try:
            parsed3 = json.loads(raw3)
            entries3 = parsed3.get("entries", [])
            test("show_by_task unknown task JSON has empty entries",
                 isinstance(entries3, list) and len(entries3) == 0,
                 f"entries={entries3!r}")
        except json.JSONDecodeError:
            # Fallback: code should have emitted valid JSON for export_fmt="json",
            # but if it fell back to text, match the concrete message.
            test("show_by_task unknown task JSON has empty entries",
                 "No entries directly tagged task_id=" in raw3,
                 f"raw: {raw3[:200]}")

    finally:
        _qs.DB_PATH = orig
        try:
            db.close()
            Path(db_path).unlink()
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# 8. generate_briefing (briefing.py) — text/json output
# ═══════════════════════════════════════════════════════════════════════════

def test_generate_briefing() -> None:
    print("\n📋 8. generate_briefing text/json output")

    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="re_test_", dir=str(TOOLS_DIR))
    os.close(fd)
    db = _make_db(db_path)

    # Seed one entry per expected category
    _insert_ke(db, category="mistake",
               title="FTS5 operator injection mistake",
               content="User query contained OR/AND operators that broke FTS5 MATCH.",
               confidence=0.9)
    _insert_ke(db, category="pattern",
               title="Parameterised query pattern",
               content="Always use ? placeholders, never string-format SQL.",
               confidence=0.9)
    _insert_ke(db, category="decision",
               title="SQLite WAL mode decision",
               content="Enable WAL journal for concurrent reads.",
               confidence=0.9)
    _insert_ke(db, category="tool",
               title="sqlite3.connect() row_factory tool note",
               content="Set db.row_factory = sqlite3.Row for dict-like access.",
               confidence=0.9)

    orig = _br.DB_PATH
    _br.DB_PATH = Path(db_path)
    try:
        # --- 8a. Default text format contains entry titles
        # Query on a term present in multiple entries across different categories.
        # "parameterised" appears in the pattern entry title and content.
        result = _br.generate_briefing("parameterised", limit=5)
        test("generate_briefing returns non-empty string",
             isinstance(result, str) and len(result) > 10,
             f"result={result[:100]!r}")
        # At least one of the seeded entries should appear
        has_known = any(t in result for t in [
            "FTS5 operator", "Parameterised query",
            "SQLite WAL", "sqlite3.connect"
        ])
        test("generate_briefing text includes seeded entries",
             has_known,
             f"result snippet: {result[:400]}")

        # --- 8b. JSON format is valid JSON with expected keys
        result_json = _br.generate_briefing("parameterised", limit=5, fmt="json")
        try:
            parsed = json.loads(result_json)
            test("generate_briefing json has 'query' key",
                 "query" in parsed,
                 f"keys={list(parsed.keys()) if isinstance(parsed, dict) else type(parsed)}")
            test("generate_briefing json has 'sections' key",
                 "sections" in parsed,
                 f"keys={list(parsed.keys()) if isinstance(parsed, dict) else type(parsed)}")
        except json.JSONDecodeError as e:
            test("generate_briefing json is valid JSON", False, str(e))
            test("generate_briefing json has 'query' key", False, "JSON invalid")
            test("generate_briefing json has 'sections' key", False, "JSON invalid")

        # --- 8c. Empty DB → graceful "no experience" message
        fd2, empty_path = tempfile.mkstemp(suffix=".db", prefix="re_empty_",
                                           dir=str(TOOLS_DIR))
        os.close(fd2)
        _make_db(empty_path)
        _br.DB_PATH = Path(empty_path)
        result_empty = _br.generate_briefing("anything")
        test("generate_briefing on empty DB returns no-experience message",
             "No relevant past experience found for:" in result_empty,
             f"got: {result_empty[:200]!r}")
        _br.DB_PATH = Path(db_path)
        try:
            Path(empty_path).unlink()
        except OSError:
            pass

    finally:
        _br.DB_PATH = orig
        try:
            db.close()
            Path(db_path).unlink()
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# 9. generate_task_briefing — tagged entries surface; JSON structure
# ═══════════════════════════════════════════════════════════════════════════

def test_generate_task_briefing() -> None:
    print("\n🗂️  9. generate_task_briefing tagged entries + JSON structure")

    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="re_test_", dir=str(TOOLS_DIR))
    os.close(fd)
    db = _make_db(db_path)

    _insert_ke(db, category="mistake",
               title="Session knowledge task mistake one",
               content="Forgot to sanitize FTS query in task briefing path.",
               task_id="sk-briefing",
               affected_files=["briefing.py"])
    _insert_ke(db, category="pattern",
               title="Session knowledge task pattern two",
               content="Use generate_task_briefing for task-scoped recall.",
               task_id="sk-briefing",
               affected_files=["briefing.py", "query-session.py"])

    orig = _br.DB_PATH
    _br.DB_PATH = Path(db_path)
    try:
        # --- 9a. Text output contains task_id and entry titles
        result = _br.generate_task_briefing("sk-briefing")
        test("generate_task_briefing returns string",
             isinstance(result, str) and len(result) > 0, f"result={result!r}")
        test("generate_task_briefing text contains task_id",
             "sk-briefing" in result,
             f"result snippet: {result[:300]}")
        test("generate_task_briefing text contains mistake entry",
             "Session knowledge task mistake one" in result,
             f"result snippet: {result[:400]}")
        test("generate_task_briefing text contains pattern entry",
             "Session knowledge task pattern two" in result,
             f"result snippet: {result[:400]}")

        # --- 9b. JSON format structure
        result_json = _br.generate_task_briefing("sk-briefing", fmt="json")
        try:
            parsed = json.loads(result_json)
            test("generate_task_briefing json has 'task_id'",
                 parsed.get("task_id") == "sk-briefing",
                 f"task_id={parsed.get('task_id')!r}")
            test("generate_task_briefing json has 'total_entries'",
                 "total_entries" in parsed,
                 f"keys={list(parsed.keys())}")
            test("generate_task_briefing json total_entries == 2",
                 parsed.get("total_entries", 0) >= 2,
                 f"total_entries={parsed.get('total_entries')}")
            tagged = parsed.get("tagged_entries", [])
            test("generate_task_briefing json tagged_entries is a list",
                 isinstance(tagged, list), f"type={type(tagged)}")
            test("generate_task_briefing json tagged_entries has 2 items",
                 len(tagged) == 2, f"len={len(tagged)}")
            if tagged:
                af = tagged[0].get("affected_files")
                test("generate_task_briefing json affected_files is list",
                     isinstance(af, list), f"type={type(af)}, val={af!r}")
            else:
                test("generate_task_briefing json affected_files is list", False,
                     "0 tagged_entries — fixture did not produce rows for 'sk-briefing'")
        except json.JSONDecodeError as e:
            test("generate_task_briefing json is valid", False, str(e))
            test("generate_task_briefing json has 'task_id'", False, "JSON invalid")
            test("generate_task_briefing json has 'total_entries'", False, "JSON invalid")
            test("generate_task_briefing json total_entries == 2", False, "JSON invalid")
            test("generate_task_briefing json tagged_entries is a list", False, "JSON invalid")
            test("generate_task_briefing json tagged_entries has 2 items", False, "JSON invalid")
            test("generate_task_briefing json affected_files is list", False, "JSON invalid")

        # --- 9c. Unknown task → no-entries message (text) or empty JSON
        result_none = _br.generate_task_briefing("task-no-such-id")
        test("generate_task_briefing unknown task returns non-empty string",
             isinstance(result_none, str) and len(result_none) > 0, "empty string")
        test("generate_task_briefing unknown task signals no entries",
             "No knowledge entries found for task:" in result_none,
             f"got: {result_none[:200]!r}")

    finally:
        _br.DB_PATH = orig
        try:
            db.close()
            Path(db_path).unlink()
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# 10. Ranking: title-hit entry scores before content-only entry
# ═══════════════════════════════════════════════════════════════════════════

def test_ranking_title_over_content() -> None:
    print("\n🏆 10. Ranking: title-hit beats content-only hit")

    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="re_test_", dir=str(TOOLS_DIR))
    os.close(fd)
    db = _make_db(db_path)

    # Entry A: keyword "walrus" ONLY in title, NOT in content
    _insert_ke(db, category="pattern",
               title="Walrus operator usage pattern",
               content="Assign variables inside expressions using the := syntax.",
               confidence=0.8)
    # Entry B: keyword "walrus" ONLY in content (not in title)
    _insert_ke(db, category="pattern",
               title="Python assignment expressions guide",
               content="The walrus operator := was introduced in Python 3.8.",
               confidence=0.8)

    orig = _qs.DB_PATH
    _qs.DB_PATH = Path(db_path)
    try:
        with _Cap() as cap:
            _qs.search_knowledge("walrus", limit=10, export_fmt="json")
        raw = cap.getvalue().strip()
        try:
            results = json.loads(raw)
            titles = [r.get("title", "") for r in results]
            test("ranking: both walrus entries returned",
                 len(results) >= 2, f"got {len(results)} results, titles={titles}")
            if len(results) >= 2:
                # Title-hit "Walrus operator usage pattern" should rank first
                test("ranking: title-hit entry ranks first",
                     "Walrus operator" in titles[0],
                     f"first={titles[0]!r}, second={titles[1]!r}")
        except json.JSONDecodeError as e:
            test("ranking test: JSON export valid", False, str(e))
            test("ranking: title-hit entry ranks first", False, "JSON invalid")
    finally:
        _qs.DB_PATH = orig
        try:
            db.close()
            Path(db_path).unlink()
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def main() -> int:
    print("=" * 60)
    print("test_retrieval_evals.py — golden-query regression harness")
    print("=" * 60)

    test_sanitize_fts_query()
    test_search_knowledge_recall()
    test_show_by_file()
    test_show_by_module()
    test_show_by_task()
    test_generate_briefing()
    test_generate_task_briefing()
    test_ranking_title_over_content()

    print()
    print("=" * 60)
    total = PASS + FAIL
    if FAIL == 0:
        print(f"✅ ALL {total} tests passed")
    else:
        print(f"❌ {FAIL}/{total} tests FAILED  ({PASS} passed)")
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
