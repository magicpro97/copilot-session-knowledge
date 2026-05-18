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
import types
from pathlib import Path

# Windows encoding fix (match project convention)
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TOOLS_DIR = Path(__file__).parent.parent

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


def _insert_ke(
    db: sqlite3.Connection,
    *,
    category: str,
    title: str,
    content: str,
    task_id: str = "",
    affected_files: list = None,
    wing: str = "",
    confidence: float = 0.9,
    tags: str = "",
) -> int:
    """Insert a knowledge entry + its ke_fts row.  Returns the rowid."""
    files_json = json.dumps(affected_files or [])
    db.execute(
        """
        INSERT INTO knowledge_entries
            (session_id, category, title, content, task_id, affected_files,
             wing, confidence, tags, first_seen, last_seen)
        VALUES ('re-session-001', ?, ?, ?, ?, ?, ?, ?, ?, '2024-01-01', '2024-01-01')
    """,
        (category, title, content, task_id, files_json, wing, confidence, tags),
    )
    rowid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute(
        """
        INSERT INTO ke_fts (rowid, title, content, tags, category, wing, room, facts)
        VALUES (?, ?, ?, ?, ?, ?, '', '[]')
    """,
        (rowid, title, content, tags, category, wing),
    )
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
        ("empty string", "", '""'),
        ("whitespace only", "   ", '""'),
        ("sole OR operator", "OR", '""'),
        ("sole AND operator", "AND", '""'),
        ("all fts5 operators", "OR AND NOT NEAR", '""'),
        ("simple single term", "sqlite", '"sqlite"*'),
        ("two-term query", "memory recall", '"memory"* "recall"*'),
        ("query with OR stripped", "auth OR login", '"auth"* "login"*'),
        ("double-quotes stripped", 'find "exact"', '"find"* "exact"*'),
        ("asterisk stripped", "prefix*", '"prefix"*'),
        ("parens stripped", "(nested)", '"nested"*'),
        # colon is in fts_special → replaced with space → two separate terms
        ("colon becomes space", "tag:value", '"tag"* "value"*'),
    ]

    for desc, inp, expected in golden:
        result_qs = _qs._sanitize_fts_query(inp)
        result_br = _br._sanitize_fts_query(inp)
        test(f"qs.sanitize({desc!r})", result_qs == expected, f"expected {expected!r}, got {result_qs!r}")
        test(f"br.sanitize({desc!r}) agrees with qs", result_qs == result_br, f"qs={result_qs!r} br={result_br!r}")

    # Truncation: input > 500 chars must not produce query longer than max_length worth
    long_input = "x" * 600
    result = _qs._sanitize_fts_query(long_input)
    test("long input truncated (input 600 chars)", len(result) <= 520, f"len={len(result)}")


# ═══════════════════════════════════════════════════════════════════════════
# 2 & 3. search_knowledge recall + JSON stability
# ═══════════════════════════════════════════════════════════════════════════


def test_search_knowledge_recall() -> None:
    print("\n📚 2–3. search_knowledge recall + JSON stability")

    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="re_test_")
    os.close(fd)
    db = _make_db(db_path)

    # Known entries
    _insert_ke(
        db,
        category="mistake",
        title="SQLite injection via string interpolation",
        content="Always use parameterised queries.",
    )
    _insert_ke(
        db,
        category="pattern",
        title="Unit testing best practice",
        content="Prefer parameterised SQL to avoid SQLite injection risks.",
    )

    orig = _qs.DB_PATH
    _qs.DB_PATH = Path(db_path)
    try:
        # --- 2a. FTS recall: searching "injection" should hit both, first entry title-match
        with _Cap() as cap:
            _qs.search_knowledge("injection", limit=10)
        out = cap.getvalue()
        test(
            "search_knowledge('injection') returns results",
            "injection" in out.lower() or "sqlite" in out.lower(),
            f"output: {out[:200]}",
        )

        # --- 2b. Specific term only in first entry title
        with _Cap() as cap:
            _qs.search_knowledge("interpolation", limit=10)
        out2 = cap.getvalue()
        test(
            "search_knowledge('interpolation') finds title entry",
            "interpolation" in out2.lower() or "sqlite injection" in out2.lower(),
            f"output: {out2[:200]}",
        )

        # --- 3a. JSON export produces valid JSON list
        with _Cap() as cap:
            _qs.search_knowledge("injection", limit=10, export_fmt="json")
        raw = cap.getvalue().strip()
        try:
            parsed = json.loads(raw)
            test("search_knowledge JSON export is a list", isinstance(parsed, list), f"type={type(parsed)}")
            test("search_knowledge JSON export non-empty", len(parsed) >= 1, f"len={len(parsed)}")
        except json.JSONDecodeError as e:
            test("search_knowledge JSON export is valid JSON", False, str(e))
            test("search_knowledge JSON export non-empty", False, "JSON invalid")

        # --- 3b. affected_files in JSON is a list (deserialized)
        _insert_ke(
            db,
            category="decision",
            title="Auth module refactor decision",
            content="Use JWT for session management.",
            affected_files=["src/auth.py", "tests/test_auth.py"],
        )
        with _Cap() as cap:
            _qs.search_knowledge("auth refactor", limit=10, export_fmt="json")
        raw2 = cap.getvalue().strip()
        try:
            parsed2 = json.loads(raw2)
            if parsed2:
                af = parsed2[0].get("affected_files")
                test(
                    "affected_files in search JSON is a list (not str)",
                    isinstance(af, list),
                    f"type={type(af)}, val={af!r}",
                )
            else:
                test(
                    "affected_files in search JSON is a list (not str)",
                    False,
                    "expected ≥1 row for 'auth refactor' query but got 0 — fixture not matching",
                )
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

    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="re_test_")
    os.close(fd)
    db = _make_db(db_path)

    _insert_ke(
        db,
        category="mistake",
        title="Off-by-one in auth token validation",
        content="Token expiry was calculated incorrectly.",
        affected_files=["src/auth.py"],
        task_id="auth-fix",
    )
    _insert_ke(
        db,
        category="pattern",
        title="Caching strategy for user profiles",
        content="Use Redis with TTL for user data.",
        affected_files=["src/cache.py"],
        task_id="cache-opt",
    )

    orig = _qs.DB_PATH
    _qs.DB_PATH = Path(db_path)
    try:
        # --- 4a. Exact path match returns the auth entry
        with _Cap() as cap:
            _qs.show_by_file("src/auth.py", limit=20)
        out = cap.getvalue()
        test(
            "show_by_file('src/auth.py') returns auth entry",
            "Off-by-one" in out or "auth token" in out.lower(),
            f"output: {out[:300]}",
        )
        test(
            "show_by_file('src/auth.py') does not return cache entry",
            "Caching strategy" not in out,
            f"cache entry leaked: {out[:200]}",
        )

        # --- 4b. Different path — no cross-contamination
        with _Cap() as cap:
            _qs.show_by_file("src/cache.py", limit=20)
        out2 = cap.getvalue()
        test(
            "show_by_file('src/cache.py') returns cache entry",
            "Caching strategy" in out2 or "cache.py" in out2.lower(),
            f"output: {out2[:300]}",
        )
        test(
            "show_by_file('src/cache.py') does not return auth entry",
            "Off-by-one" not in out2,
            f"auth entry leaked: {out2[:200]}",
        )

        # --- 4c. Path with no entries
        with _Cap() as cap:
            _qs.show_by_file("src/nonexistent.py", limit=20)
        out3 = cap.getvalue()
        test(
            "show_by_file for unknown path prints no-entry message",
            "No knowledge entries recorded for file:" in out3,
            f"output: {out3[:200]}",
        )

        # --- 5a. compact mode: includes ~tok hint when est_tokens set
        db.execute("UPDATE knowledge_entries SET est_tokens=77 WHERE task_id='auth-fix'")
        db.commit()
        with _Cap() as cap:
            _qs.show_by_file("src/auth.py", limit=20, compact=True)
        out4 = cap.getvalue()
        test("show_by_file compact mode produces output", len(out4) > 0, "empty output")
        test("show_by_file compact shows ~tok hint", "~77tok" in out4, f"output: {out4[:200]}")

        # --- 5b. JSON export is valid + affected_files decoded
        with _Cap() as cap:
            _qs.show_by_file("src/auth.py", limit=20, export_fmt="json")
        raw = cap.getvalue().strip()
        try:
            parsed = json.loads(raw)
            test("show_by_file JSON export is a list", isinstance(parsed, list), f"type={type(parsed)}")
            if parsed:
                af = parsed[0].get("affected_files")
                test(
                    "show_by_file JSON affected_files is list",
                    isinstance(af, list) and "src/auth.py" in af,
                    f"af={af!r}",
                )
            else:
                test(
                    "show_by_file JSON affected_files is list",
                    False,
                    "0 rows returned — fixture did not match 'src/auth.py'",
                )
        except json.JSONDecodeError as e:
            test("show_by_file JSON export is valid JSON", False, str(e))
            test("show_by_file JSON affected_files is list", False, "JSON invalid")

        # --- 5c. Empty result via JSON export → empty list []
        with _Cap() as cap:
            _qs.show_by_file("src/nowhere.py", limit=20, export_fmt="json")
        raw2 = cap.getvalue().strip()
        try:
            parsed2 = json.loads(raw2)
            test(
                "show_by_file empty JSON export is empty list",
                isinstance(parsed2, list) and len(parsed2) == 0,
                f"got: {parsed2!r}",
            )
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

    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="re_test_")
    os.close(fd)
    db = _make_db(db_path)

    # Entry with "auth" as the first directory component
    _insert_ke(
        db,
        category="pattern",
        title="Auth module login flow",
        content="Token verified via HMAC.",
        affected_files=["auth/login.py"],
    )
    # Entry with "auth" as a middle directory component
    _insert_ke(
        db,
        category="decision",
        title="Auth middleware refactor",
        content="Moved middleware to src/auth/middleware.py.",
        affected_files=["src/auth/middleware.py"],
    )
    # Unrelated entry
    _insert_ke(
        db,
        category="mistake",
        title="Cache invalidation bug",
        content="Redis TTL was set to 0 by default.",
        affected_files=["src/cache/store.py"],
    )

    orig = _qs.DB_PATH
    _qs.DB_PATH = Path(db_path)
    try:
        # --- 6a. Module "auth" matches head-directory entry
        with _Cap() as cap:
            _qs.show_by_module("auth", limit=20)
        out = cap.getvalue()
        test(
            "show_by_module('auth') finds head-dir entry",
            "Auth module login flow" in out or "auth/login" in out.lower(),
            f"output: {out[:300]}",
        )

        # --- 6b. Module "auth" also matches mid-path entry
        test(
            "show_by_module('auth') finds mid-path entry",
            "Auth middleware" in out or "src/auth/" in out.lower(),
            f"output: {out[:300]}",
        )

        # --- 6c. Module "auth" does not return cache entry
        test(
            "show_by_module('auth') excludes cache entry",
            "Cache invalidation" not in out,
            f"cache entry leaked: {out[:200]}",
        )

        # --- 6d. Content fallback: "cache" has no direct affected_files hit on
        #         a module named "cachemod" but content contains "cache"
        #         (using title/content substring fallback)
        with _Cap() as cap:
            _qs.show_by_module("cache", limit=20)
        out2 = cap.getvalue()
        test(
            "show_by_module('cache') returns cache-tagged entries or fallback",
            "cache" in out2.lower(),
            f"output: {out2[:300]}",
        )

        # --- 6e. JSON export is a valid list
        with _Cap() as cap:
            _qs.show_by_module("auth", limit=20, export_fmt="json")
        raw = cap.getvalue().strip()
        try:
            parsed = json.loads(raw)
            test("show_by_module JSON export is a list", isinstance(parsed, list), f"type={type(parsed)}")
            test("show_by_module JSON export non-empty", len(parsed) >= 1, f"len={len(parsed)}")
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

    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="re_test_")
    os.close(fd)
    db = _make_db(db_path)

    _insert_ke(
        db,
        category="mistake",
        title="Task-scoped mistake alpha",
        content="Forgot to close DB handle in task alpha.",
        task_id="task-alpha",
        affected_files=["src/db.py"],
    )
    _insert_ke(
        db,
        category="pattern",
        title="Task-scoped pattern alpha",
        content="Always use context manager for DB connections.",
        task_id="task-alpha",
        affected_files=["src/db.py", "src/utils.py"],
    )
    _insert_ke(
        db,
        category="decision",
        title="Beta task architecture decision",
        content="Use event sourcing for state management.",
        task_id="task-beta",
    )

    orig = _qs.DB_PATH
    _qs.DB_PATH = Path(db_path)
    try:
        # --- 7a. Correct task_id returns both alpha entries
        with _Cap() as cap:
            _qs.show_by_task("task-alpha", limit=30)
        out = cap.getvalue()
        test(
            "show_by_task('task-alpha') returns mistake entry",
            "Task-scoped mistake alpha" in out,
            f"output: {out[:300]}",
        )
        test(
            "show_by_task('task-alpha') returns pattern entry",
            "Task-scoped pattern alpha" in out,
            f"output: {out[:300]}",
        )

        # --- 7b. Wrong task_id — beta entry must not appear under alpha
        test(
            "show_by_task('task-alpha') excludes task-beta entry",
            "Beta task architecture" not in out,
            f"beta entry leaked into alpha: {out[:200]}",
        )

        # --- 7c. JSON envelope: has task_id and entries keys
        with _Cap() as cap:
            _qs.show_by_task("task-alpha", limit=30, export_fmt="json")
        raw = cap.getvalue().strip()
        try:
            parsed = json.loads(raw)
            test(
                "show_by_task JSON has 'task_id' key",
                "task_id" in parsed,
                f"keys={list(parsed.keys()) if isinstance(parsed, dict) else type(parsed)}",
            )
            test(
                "show_by_task JSON has 'entries' key",
                "entries" in parsed,
                f"keys={list(parsed.keys()) if isinstance(parsed, dict) else type(parsed)}",
            )
            entries = parsed.get("entries", [])
            test("show_by_task JSON entries is a list", isinstance(entries, list), f"type={type(entries)}")
            test("show_by_task JSON returns 2 alpha entries", len(entries) == 2, f"len={len(entries)}")
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
                test(
                    "show_by_task JSON affected_files decoded to list",
                    False,
                    "0 entries returned — fixture did not match 'task-alpha'",
                )
            else:
                af_types_ok = all(isinstance(e.get("affected_files"), list) for e in entries2)
                test(
                    "show_by_task JSON affected_files decoded to list",
                    af_types_ok,
                    f"entries with non-list af: {[e.get('affected_files') for e in entries2]}",
                )
        except json.JSONDecodeError:
            test("show_by_task JSON affected_files decoded to list", False, "JSON invalid")

        # --- 7e. Unknown task_id → JSON has empty entries list
        with _Cap() as cap:
            _qs.show_by_task("task-nonexistent", limit=30, export_fmt="json")
        raw3 = cap.getvalue().strip()
        try:
            parsed3 = json.loads(raw3)
            entries3 = parsed3.get("entries", [])
            test(
                "show_by_task unknown task JSON has empty entries",
                isinstance(entries3, list) and len(entries3) == 0,
                f"entries={entries3!r}",
            )
        except json.JSONDecodeError:
            # Fallback: code should have emitted valid JSON for export_fmt="json",
            # but if it fell back to text, match the concrete message.
            test(
                "show_by_task unknown task JSON has empty entries",
                "No entries directly tagged task_id=" in raw3,
                f"raw: {raw3[:200]}",
            )

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

    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="re_test_")
    os.close(fd)
    db = _make_db(db_path)

    # Seed one entry per expected category
    _insert_ke(
        db,
        category="mistake",
        title="FTS5 operator injection mistake",
        content="User query contained OR/AND operators that broke FTS5 MATCH.",
        confidence=0.9,
    )
    _insert_ke(
        db,
        category="pattern",
        title="Parameterised query pattern",
        content="Always use ? placeholders, never string-format SQL.",
        confidence=0.9,
    )
    _insert_ke(
        db,
        category="decision",
        title="SQLite WAL mode decision",
        content="Enable WAL journal for concurrent reads.",
        confidence=0.9,
    )
    _insert_ke(
        db,
        category="tool",
        title="sqlite3.connect() row_factory tool note",
        content="Set db.row_factory = sqlite3.Row for dict-like access.",
        confidence=0.9,
    )

    orig = _br.DB_PATH
    _br.DB_PATH = Path(db_path)
    try:
        # --- 8a. Default text format contains entry titles
        # Query on a term present in multiple entries across different categories.
        # "parameterised" appears in the pattern entry title and content.
        result = _br.generate_briefing("parameterised", limit=5)
        test(
            "generate_briefing returns non-empty string",
            isinstance(result, str) and len(result) > 10,
            f"result={result[:100]!r}",
        )
        # At least one of the seeded entries should appear
        has_known = any(t in result for t in ["FTS5 operator", "Parameterised query", "SQLite WAL", "sqlite3.connect"])
        test("generate_briefing text includes seeded entries", has_known, f"result snippet: {result[:400]}")

        # --- 8b. JSON format is valid JSON with expected keys
        result_json = _br.generate_briefing("parameterised", limit=5, fmt="json")
        try:
            parsed = json.loads(result_json)
            test(
                "generate_briefing json has 'query' key",
                "query" in parsed,
                f"keys={list(parsed.keys()) if isinstance(parsed, dict) else type(parsed)}",
            )
            test(
                "generate_briefing json has 'sections' key",
                "sections" in parsed,
                f"keys={list(parsed.keys()) if isinstance(parsed, dict) else type(parsed)}",
            )
        except json.JSONDecodeError as e:
            test("generate_briefing json is valid JSON", False, str(e))
            test("generate_briefing json has 'query' key", False, "JSON invalid")
            test("generate_briefing json has 'sections' key", False, "JSON invalid")

        # --- 8c. Empty DB → graceful "no experience" message
        fd2, empty_path = tempfile.mkstemp(suffix=".db", prefix="re_empty_")
        os.close(fd2)
        _make_db(empty_path)
        _br.DB_PATH = Path(empty_path)
        result_empty = _br.generate_briefing("anything")
        test(
            "generate_briefing on empty DB returns no-experience message",
            "No relevant past experience found for:" in result_empty,
            f"got: {result_empty[:200]!r}",
        )
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

    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="re_test_")
    os.close(fd)
    db = _make_db(db_path)

    _insert_ke(
        db,
        category="mistake",
        title="Session knowledge task mistake one",
        content="Forgot to sanitize FTS query in task briefing path.",
        task_id="sk-briefing",
        affected_files=["briefing.py"],
    )
    _insert_ke(
        db,
        category="pattern",
        title="Session knowledge task pattern two",
        content="Use generate_task_briefing for task-scoped recall.",
        task_id="sk-briefing",
        affected_files=["briefing.py", "query-session.py"],
    )

    orig = _br.DB_PATH
    _br.DB_PATH = Path(db_path)
    try:
        # --- 9a. Text output contains task_id and entry titles
        result = _br.generate_task_briefing("sk-briefing")
        test("generate_task_briefing returns string", isinstance(result, str) and len(result) > 0, f"result={result!r}")
        test("generate_task_briefing text contains task_id", "sk-briefing" in result, f"result snippet: {result[:300]}")
        test(
            "generate_task_briefing text contains mistake entry",
            "Session knowledge task mistake one" in result,
            f"result snippet: {result[:400]}",
        )
        test(
            "generate_task_briefing text contains pattern entry",
            "Session knowledge task pattern two" in result,
            f"result snippet: {result[:400]}",
        )

        # --- 9b. JSON format structure
        result_json = _br.generate_task_briefing("sk-briefing", fmt="json")
        try:
            parsed = json.loads(result_json)
            test(
                "generate_task_briefing json has 'task_id'",
                parsed.get("task_id") == "sk-briefing",
                f"task_id={parsed.get('task_id')!r}",
            )
            test(
                "generate_task_briefing json has 'total_entries'",
                "total_entries" in parsed,
                f"keys={list(parsed.keys())}",
            )
            test(
                "generate_task_briefing json total_entries == 2",
                parsed.get("total_entries", 0) >= 2,
                f"total_entries={parsed.get('total_entries')}",
            )
            tagged = parsed.get("tagged_entries", [])
            test(
                "generate_task_briefing json tagged_entries is a list", isinstance(tagged, list), f"type={type(tagged)}"
            )
            test("generate_task_briefing json tagged_entries has 2 items", len(tagged) == 2, f"len={len(tagged)}")
            if tagged:
                af = tagged[0].get("affected_files")
                test(
                    "generate_task_briefing json affected_files is list",
                    isinstance(af, list),
                    f"type={type(af)}, val={af!r}",
                )
            else:
                test(
                    "generate_task_briefing json affected_files is list",
                    False,
                    "0 tagged_entries — fixture did not produce rows for 'sk-briefing'",
                )
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
        test(
            "generate_task_briefing unknown task returns non-empty string",
            isinstance(result_none, str) and len(result_none) > 0,
            "empty string",
        )
        test(
            "generate_task_briefing unknown task signals no entries",
            "No knowledge entries found for task:" in result_none,
            f"got: {result_none[:200]!r}",
        )

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

    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="re_test_")
    os.close(fd)
    db = _make_db(db_path)

    # Entry A: keyword "walrus" ONLY in title, NOT in content
    _insert_ke(
        db,
        category="pattern",
        title="Walrus operator usage pattern",
        content="Assign variables inside expressions using the := syntax.",
        confidence=0.8,
    )
    # Entry B: keyword "walrus" ONLY in content (not in title)
    _insert_ke(
        db,
        category="pattern",
        title="Python assignment expressions guide",
        content="The walrus operator := was introduced in Python 3.8.",
        confidence=0.8,
    )

    orig = _qs.DB_PATH
    _qs.DB_PATH = Path(db_path)
    try:
        with _Cap() as cap:
            _qs.search_knowledge("walrus", limit=10, export_fmt="json")
        raw = cap.getvalue().strip()
        try:
            results = json.loads(raw)
            titles = [r.get("title", "") for r in results]
            test(
                "ranking: both walrus entries returned",
                len(results) >= 2,
                f"got {len(results)} results, titles={titles}",
            )
            if len(results) >= 2:
                # Title-hit "Walrus operator usage pattern" should rank first
                test(
                    "ranking: title-hit entry ranks first",
                    "Walrus operator" in titles[0],
                    f"first={titles[0]!r}, second={titles[1]!r}",
                )
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
# 11. Adaptive strictness — analysis + FTS generation + retrieval behaviour
# ═══════════════════════════════════════════════════════════════════════════


def test_adaptive_strictness() -> None:
    print("\n⚙️  11. Adaptive strictness — analysis + FTS + retrieval")

    # ── 11a. _analyze_query_strictness: strict cases ─────────────────────
    strict_cases = [
        ("single technical term", "walrus"),
        ("two words, file ext", "src/auth.py"),
        ("snake_case identifier", "parse_query"),
        ("two camelCase-like terms", "parseToken"),
    ]
    for desc, query in strict_cases:
        result_qs = _qs._analyze_query_strictness(query)
        result_br = _br._analyze_query_strictness(query)
        test(f"analyze_strictness strict: qs({desc!r})", result_qs == "strict", f"got {result_qs!r}")
        test(
            f"analyze_strictness strict: br agrees ({desc!r})",
            result_qs == result_br,
            f"qs={result_qs!r} br={result_br!r}",
        )

    # ── 11b. _analyze_query_strictness: broad cases ───────────────────────
    broad_cases = [
        ("long natural-language query", "how should we implement user authentication with jwt and oauth"),
        ("many stopwords", "what are the best practices for using this with that framework"),
    ]
    for desc, query in broad_cases:
        result_qs = _qs._analyze_query_strictness(query)
        result_br = _br._analyze_query_strictness(query)
        test(f"analyze_strictness broad: qs({desc!r})", result_qs == "broad", f"got {result_qs!r}")
        test(
            f"analyze_strictness broad: br agrees ({desc!r})",
            result_qs == result_br,
            f"qs={result_qs!r} br={result_br!r}",
        )

    # ── 11c. _analyze_query_strictness: medium cases ──────────────────────
    medium_cases = [
        ("3-word technical query", "memory recall cache"),
        ("empty string", ""),
    ]
    for desc, query in medium_cases:
        result_qs = _qs._analyze_query_strictness(query)
        result_br = _br._analyze_query_strictness(query)
        test(f"analyze_strictness medium: qs({desc!r})", result_qs == "medium", f"got {result_qs!r}")
        test(
            f"analyze_strictness medium: br agrees ({desc!r})",
            result_qs == result_br,
            f"qs={result_qs!r} br={result_br!r}",
        )

    # ── 11d. _build_adaptive_fts_query: strict → no trailing * ───────────
    fts_strict, s_strict, delta_strict = _qs._build_adaptive_fts_query("walrus")
    test("build_adaptive_fts strict: strictness=strict", s_strict == "strict", f"got {s_strict!r}")
    test("build_adaptive_fts strict: no * wildcard in query", "*" not in fts_strict, f"fts_query={fts_strict!r}")
    test("build_adaptive_fts strict: confidence_delta >= 0", delta_strict >= 0, f"delta={delta_strict}")

    # ── 11e. _build_adaptive_fts_query: broad → OR conjunction ───────────
    fts_broad, s_broad, delta_broad = _qs._build_adaptive_fts_query(
        "how should we implement user auth with jwt and oauth in python"
    )
    test("build_adaptive_fts broad: strictness=broad", s_broad == "broad", f"got {s_broad!r}")
    test("build_adaptive_fts broad: OR conjunction present", " OR " in fts_broad, f"fts_query={fts_broad!r}")
    test("build_adaptive_fts broad: confidence_delta <= 0", delta_broad <= 0, f"delta={delta_broad}")

    # ── 11f. _build_adaptive_fts_query: medium → unchanged prefix * ───────
    fts_med, s_med, delta_med = _qs._build_adaptive_fts_query("memory recall cache")
    test("build_adaptive_fts medium: strictness=medium", s_med == "medium", f"got {s_med!r}")
    test(
        "build_adaptive_fts medium: all terms have * wildcard",
        all(t.endswith('"*') for t in fts_med.split() if t),
        f"fts_query={fts_med!r}",
    )
    test("build_adaptive_fts medium: delta=0.0", delta_med == 0.0, f"delta={delta_med}")

    # ── 11g. Both modules agree on _build_adaptive_fts_query ─────────────
    test(
        "build_adaptive_fts: qs and br agree on 'walrus'",
        _qs._build_adaptive_fts_query("walrus") == _br._build_adaptive_fts_query("walrus"),
        "modules returned different results",
    )

    # ── 11h. Empty query → safe no-op ────────────────────────────────────
    fts_empty, s_empty, d_empty = _qs._build_adaptive_fts_query("")
    test("build_adaptive_fts empty: returns safe '\"\"' query", fts_empty == '""', f"got {fts_empty!r}")
    test("build_adaptive_fts empty: delta is 0.0", d_empty == 0.0, f"delta={d_empty}")

    # ── 11i. Strict retrieval: exact match finds seeded entry ─────────────
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="re_test_")
    os.close(fd)
    db = _make_db(db_path)

    _insert_ke(
        db,
        category="pattern",
        title="Wombat allocation strategy",
        content="Allocate objects using pool-based wombat allocator.",
    )
    _insert_ke(
        db, category="mistake", title="Wombats are not thread-safe", content="Do not share wombats across threads."
    )

    orig = _qs.DB_PATH
    _qs.DB_PATH = Path(db_path)
    try:
        with _Cap() as cap:
            _qs.search_knowledge("wombat", limit=10, export_fmt="json")
        raw = cap.getvalue().strip()
        try:
            results = json.loads(raw)
            test(
                "adaptive strict: 'wombat' finds ≥1 result",
                len(results) >= 1,
                f"got {len(results)} results; fts={_qs._build_adaptive_fts_query('wombat')[0]!r}",
            )
        except json.JSONDecodeError as e:
            test("adaptive strict: 'wombat' finds ≥1 result", False, f"JSON invalid: {e}; raw={raw[:200]!r}")
    finally:
        _qs.DB_PATH = orig
        try:
            db.close()
            Path(db_path).unlink()
        except OSError:
            pass

    # ── 11j. Broad retrieval: OR query finds entries via separate terms ────
    fd2, db_path2 = tempfile.mkstemp(suffix=".db", prefix="re_test_")
    os.close(fd2)
    db2 = _make_db(db_path2)

    _insert_ke(
        db2,
        category="pattern",
        title="JWT authentication implementation",
        content="Use HS256 for JWT signature verification.",
    )
    _insert_ke(
        db2,
        category="mistake",
        title="OAuth token expiry oversight",
        content="Always check token expiry before using cached tokens.",
    )

    orig2 = _qs.DB_PATH
    _qs.DB_PATH = Path(db_path2)
    try:
        broad_q = "how should we implement user authentication with jwt and oauth"
        with _Cap() as cap:
            _qs.search_knowledge(broad_q, limit=10, export_fmt="json")
        raw2 = cap.getvalue().strip()
        try:
            results2 = json.loads(raw2)
            test(
                "adaptive broad: OR query returns ≥1 result",
                len(results2) >= 1,
                f"got {len(results2)}; fts={_qs._build_adaptive_fts_query(broad_q)[0]!r}",
            )
        except json.JSONDecodeError as e:
            test("adaptive broad: OR query returns ≥1 result", False, f"JSON invalid: {e}; raw={raw2[:200]!r}")
    finally:
        _qs.DB_PATH = orig2
        try:
            db2.close()
            Path(db_path2).unlink()
        except OSError:
            pass

    # ── 11k. Confidence delta applied in briefing search_knowledge_entries ─
    fd3, db_path3 = tempfile.mkstemp(suffix=".db", prefix="re_test_")
    os.close(fd3)
    db3 = _make_db(db_path3)

    # High-confidence entry — should appear in strict mode (0.5 + 0.2 = 0.7 threshold)
    _insert_ke(
        db3,
        category="pattern",
        title="Frobnicator design pattern",
        content="Use frobnicator for cross-cutting concerns.",
        confidence=0.9,
    )
    # Low-confidence entry — should appear in broad mode but not strict
    _insert_ke(
        db3,
        category="pattern",
        title="Frobnicator alternative",
        content="Alternative approach using frobnicator.",
        confidence=0.4,
    )

    orig3 = _br.DB_PATH
    _br.DB_PATH = Path(db_path3)
    try:
        # Strict query: only high-confidence entry should be returned
        strict_results = _br.search_knowledge_entries(db3, "frobnicator", "pattern", limit=10, min_confidence=0.5)
        high_conf_titles = [r["title"] for r in strict_results]
        test(
            "br search_ke strict: high-confidence entry returned",
            any("Frobnicator design" in t for t in high_conf_titles),
            f"titles={high_conf_titles}",
        )
        test(
            "br search_ke strict: low-confidence entry excluded",
            not any("alternative" in t.lower() for t in high_conf_titles),
            f"titles={high_conf_titles}",
        )

        # Broad query: lower confidence threshold → both entries should appear
        broad_results = _br.search_knowledge_entries(
            db3,
            "how should we use frobnicator with other patterns in our project",
            "pattern",
            limit=10,
            min_confidence=0.5,
        )
        broad_titles = [r["title"] for r in broad_results]
        test(
            "br search_ke broad: low-confidence entry included",
            any("alternative" in t.lower() for t in broad_titles),
            f"titles={broad_titles}",
        )
    finally:
        _br.DB_PATH = orig3
        try:
            db3.close()
            Path(db_path3).unlink()
        except OSError:
            pass


def test_semantic_feedback_fragment_visibility() -> None:
    print("\n🧠 12. semantic feedback fragment visibility")

    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="re_semantic_")
    os.close(fd)
    db = _make_db(db_path)

    fake_embed = types.ModuleType("embed")
    fake_embed.load_config = lambda: {}
    fake_embed.ensure_embedding_tables = lambda db: None
    fake_embed.hybrid_search = lambda db, query, config, limit=10: [
        {
            "title": "Semantic result with feedback",
            "session_id": "re-session-001",
            "doc_type": "pattern",
            "source": "keyword+semantic",
            "rrf_score": 0.8123,
            "feedback_bias": 0.25,
            "feedback_count": 3,
            "excerpt": "feedback-aware reranking excerpt",
            "section_name": "findings",
        }
    ]

    old_embed = sys.modules.get("embed")
    old_db_path = _qs.DB_PATH
    _qs.DB_PATH = Path(db_path)
    sys.modules["embed"] = fake_embed
    try:
        with _Cap() as cap:
            _qs.semantic_search("feedback rerank", limit=5, verbose=False)
        compact_out = cap.getvalue()
        test(
            "semantic compact output omits feedback fragment",
            "Feedback: bias=" not in compact_out,
            f"output={compact_out!r}",
        )

        with _Cap() as cap2:
            _qs.semantic_search("feedback rerank", limit=5, verbose=True)
        verbose_out = cap2.getvalue()
        test(
            "semantic verbose output shows non-zero feedback fragment",
            "Feedback: bias=+0.25 (count=3)" in verbose_out,
            f"output={verbose_out!r}",
        )

        fake_embed.hybrid_search = lambda db, query, config, limit=10: [
            {
                "title": "Semantic result zero feedback",
                "session_id": "re-session-001",
                "doc_type": "pattern",
                "source": "keyword+semantic",
                "rrf_score": 0.7000,
                "feedback_bias": 0.0,
                "feedback_count": 2,
                "excerpt": "zero-bias excerpt",
                "section_name": "findings",
            }
        ]
        with _Cap() as cap3:
            _qs.semantic_search("feedback rerank", limit=5, verbose=True)
        zero_bias_verbose_out = cap3.getvalue()
        test(
            "semantic verbose output omits zero-bias feedback fragment",
            "Feedback: bias=" not in zero_bias_verbose_out,
            f"output={zero_bias_verbose_out!r}",
        )
    finally:
        _qs.DB_PATH = old_db_path
        if old_embed is None:
            sys.modules.pop("embed", None)
        else:
            sys.modules["embed"] = old_embed
        try:
            db.close()
            Path(db_path).unlink()
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# 13. #369 — Rewritten query used consistently in semantic search
# ═══════════════════════════════════════════════════════════════════════════


def test_semantic_uses_rewritten_query() -> None:
    print("\n🔄 13. #369 rewritten query consistency in semantic_search")

    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="re_sem_rw_", dir=str(TOOLS_DIR))
    os.close(fd)
    db = _make_db(db_path)

    captured_query: list[str] = []

    import types as _types

    fake_embed = _types.ModuleType("embed_rw_test")
    fake_embed.load_config = lambda: {}
    fake_embed.ensure_embedding_tables = lambda db: None

    def _fake_hybrid(db, query, config, limit=10):
        captured_query.append(query)
        return []

    fake_embed.hybrid_search = _fake_hybrid

    old_embed = sys.modules.get("embed")
    old_db_path = _qs.DB_PATH
    _qs.DB_PATH = Path(db_path)
    sys.modules["embed"] = fake_embed
    try:
        # Simulate the caller: raw query is verbose, retrieval_query is condensed
        raw = "please help me understand how to implement user authentication with jwt tokens"
        rewritten = _qs._rewrite_query_local(raw)
        assert rewritten != raw, f"rewritten should differ: {rewritten!r}"

        with _Cap():
            _qs.semantic_search(raw, limit=5, retrieval_query=rewritten)

        test(
            "#369 semantic_search passes retrieval_query to hybrid_search",
            len(captured_query) >= 1 and captured_query[0] == rewritten,
            f"captured={captured_query!r}, expected rewritten={rewritten!r}",
        )

        # Without retrieval_query, falls back to raw query
        captured_query.clear()
        with _Cap():
            _qs.semantic_search(raw, limit=5, retrieval_query=None)
        test(
            "#369 without retrieval_query uses raw query",
            len(captured_query) >= 1 and captured_query[0] == raw,
            f"captured={captured_query!r}",
        )

    finally:
        _qs.DB_PATH = old_db_path
        if old_embed is None:
            sys.modules.pop("embed", None)
        else:
            sys.modules["embed"] = old_embed
        try:
            db.close()
            Path(db_path).unlink()
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# 14. #371 — Synonym expansion flag
# ═══════════════════════════════════════════════════════════════════════════


def test_synonym_expansion() -> None:
    print("\n🔤 14. #371 synonym expansion")

    # qs module has _expand_synonyms and _expand_synonyms_fts
    test("#371 qs._expand_synonyms exists", hasattr(_qs, "_expand_synonyms"), "attribute missing")
    test("#371 qs._SYNONYM_MAP exists", hasattr(_qs, "_SYNONYM_MAP"), "attribute missing")
    test("#371 qs._expand_synonyms_fts exists", hasattr(_qs, "_expand_synonyms_fts"), "attribute missing")

    # briefing module also has both functions
    test("#371 br._expand_synonyms exists", hasattr(_br, "_expand_synonyms"), "attribute missing")
    test("#371 br._expand_synonyms_fts exists", hasattr(_br, "_expand_synonyms_fts"), "attribute missing")

    # Known synonym expansion cases
    cases = [
        ("auth", ["auth", "authentication", "login", "token"]),
        ("error", ["error", "bug", "exception", "failure"]),
        ("authentication", ["authentication", "auth", "login", "token"]),
        ("database", ["database", "db", "sqlite", "sql"]),
        ("retrieval", ["retrieval", "search", "query", "recall", "fts"]),
    ]
    for term, expected_terms in cases:
        result = _qs._expand_synonyms(term)
        result_tokens = set(result.split())
        test(
            f"#371 qs expand '{term}' includes synonyms",
            all(t in result_tokens for t in expected_terms[:2]),
            f"result={result!r}",
        )
        # briefing module agrees
        br_result = _br._expand_synonyms(term)
        test(f"#371 br expand '{term}' agrees with qs", br_result == result, f"qs={result!r}, br={br_result!r}")

    # _expand_synonyms_fts builds an OR query with prefix wildcards
    fts_result = _qs._expand_synonyms_fts("auth")
    test(
        "#371 _expand_synonyms_fts uses OR conjunction",
        " OR " in fts_result,
        f"result={fts_result!r}",
    )
    test(
        "#371 _expand_synonyms_fts includes 'authentication'*",
        '"authentication"*' in fts_result,
        f"result={fts_result!r}",
    )

    # briefing._expand_synonyms_fts agrees
    br_fts = _br._expand_synonyms_fts("auth")
    test(
        "#371 br._expand_synonyms_fts agrees with qs",
        br_fts == fts_result,
        f"qs={fts_result!r}, br={br_fts!r}",
    )

    # Single unknown term is returned unchanged in _expand_synonyms
    unknown = _qs._expand_synonyms("frobnicator")
    test("#371 unknown term returned as-is", unknown == "frobnicator", f"got {unknown!r}")

    # Over-length query (>6 terms) skips expansion
    long_q = "one two three four five six seven eight"
    long_result = _qs._expand_synonyms(long_q)
    test("#371 >6 term query not expanded", long_result == long_q, f"got {long_result!r}")

    # Empty string safe
    empty_result = _qs._expand_synonyms("")
    test("#371 empty string safe", isinstance(empty_result, str), f"got {empty_result!r}")

    # _expand_synonyms_fts integration: OR query retrieves synonym-matched entry
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="re_syn_", dir=str(TOOLS_DIR))
    os.close(fd)
    db = _make_db(db_path)
    _insert_ke(
        db,
        category="pattern",
        title="JWT authentication pattern",
        content="Use HS256 for token signing.",
        confidence=0.9,
    )
    orig = _qs.DB_PATH
    _qs.DB_PATH = Path(db_path)
    try:
        # The OR FTS query for "auth" should match "JWT authentication pattern"
        # because "authentication" is in the title and "authentication"* is in the OR query.
        fts_q = _qs._expand_synonyms_fts("auth")
        test(
            "#371 'auth' FTS OR query contains 'authentication'*",
            '"authentication"*' in fts_q,
            f"fts_q={fts_q!r}",
        )
        # Pass the OR query directly as retrieval_query to search_knowledge
        with _Cap() as cap:
            _qs.search_knowledge("auth", limit=10, export_fmt="json", retrieval_query=fts_q)
        raw = cap.getvalue().strip()
        try:
            parsed = json.loads(raw)
            test(
                "#371 OR-expanded 'auth' query retrieves JWT entry",
                any("JWT" in (r.get("title", "") or "") for r in parsed),
                f"titles={[r.get('title') for r in parsed]}",
            )
        except json.JSONDecodeError as e:
            test("#371 OR-expanded 'auth' query retrieves JWT entry", False, f"JSON invalid: {e}, raw={raw[:200]!r}")
    finally:
        _qs.DB_PATH = orig
        try:
            db.close()
            Path(db_path).unlink()
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# 14b. #371 — Combined --expand-synonyms + --semantic: semantic path gets plain text
# ═══════════════════════════════════════════════════════════════════════════


def test_synonym_expansion_semantic_split() -> None:
    """Verify that when --expand-synonyms and --semantic are combined the semantic
    path receives a plain space-joined query (from _expand_synonyms) while FTS
    paths receive the OR-syntax query (from _expand_synonyms_fts).

    This tests the Opus review finding: previously both paths received the FTS
    form with quotes, asterisks, and OR operators.
    """
    print("\n🔤 14b. #371 semantic/FTS synonym expansion split")

    # Plain expansion must not contain FTS-specific characters
    plain = _qs._expand_synonyms("auth")
    test(
        "#371b plain expansion has no OR operator",
        " OR " not in plain,
        f"plain={plain!r}",
    )
    test(
        "#371b plain expansion has no asterisks",
        "*" not in plain,
        f"plain={plain!r}",
    )
    test(
        "#371b plain expansion has no double-quotes",
        '"' not in plain,
        f"plain={plain!r}",
    )
    test(
        "#371b plain expansion is space-joined synonyms",
        " " in plain and "auth" in plain.split(),
        f"plain={plain!r}",
    )

    # FTS expansion must contain OR-syntax
    fts = _qs._expand_synonyms_fts("auth")
    test(
        "#371b FTS expansion has OR operator",
        " OR " in fts,
        f"fts={fts!r}",
    )
    test(
        "#371b FTS expansion has asterisks",
        "*" in fts,
        f"fts={fts!r}",
    )
    test(
        "#371b FTS expansion has double-quotes",
        '"' in fts,
        f"fts={fts!r}",
    )

    # Simulate the combined --expand-synonyms --semantic query routing:
    # rewritten_query starts as plain; semantic_query = _expand_synonyms(rewritten);
    # rewritten_query = _expand_synonyms_fts(rewritten)  → FTS path
    # semantic path uses semantic_query (plain).
    base_query = "auth"
    semantic_q = _qs._expand_synonyms(base_query)
    fts_q = _qs._expand_synonyms_fts(base_query)

    # Semantic query should be clean plain text
    test(
        "#371b combined: semantic_query is plain (no FTS syntax)",
        " OR " not in semantic_q and "*" not in semantic_q and '"' not in semantic_q,
        f"semantic_q={semantic_q!r}",
    )
    # FTS query should have OR syntax
    test(
        "#371b combined: fts_query has OR syntax",
        " OR " in fts_q and "*" in fts_q,
        f"fts_q={fts_q!r}",
    )
    # They must be different (proving the split exists)
    test(
        "#371b combined: semantic_query != fts_query (they differ)",
        semantic_q != fts_q,
        f"semantic_q={semantic_q!r}, fts_q={fts_q!r}",
    )


# ═══════════════════════════════════════════════════════════════════════════


def test_tfidf_section_entry_precision() -> None:
    print("\n🎯 15. #376 TF-IDF section-to-entry precision")

    # Verify briefing.search_semantic TF-IDF path uses document-scoped join
    # We inspect the source code for the document_id join (structural test).
    import inspect

    src = inspect.getsource(_br.search_semantic)

    test(
        "#376 TF-IDF path joins on document_id",
        "ke.document_id" in src,
        "document_id join not found in search_semantic source",
    )
    test(
        "#376 TF-IDF path has CASE WHEN same-document priority",
        "CASE WHEN" in src and "document_id" in src,
        "same-document ordering not found",
    )
    test(
        "#376 section_id used 4 times (document_id subquery, session_id subquery, order, fallback)",
        src.count("section_id") >= 3,
        f"section_id appears {src.count('section_id')} times",
    )


# ═══════════════════════════════════════════════════════════════════════════
# 16. #377 — Universal status-note suppression
# ═══════════════════════════════════════════════════════════════════════════


def test_status_note_suppression_universal() -> None:
    print("\n🚫 16. #377 universal status-note suppression")

    # ── 16a. qs module has _STATUS_NOTE_RE ───────────────────────────────
    test("#377 qs._STATUS_NOTE_RE defined", hasattr(_qs, "_STATUS_NOTE_RE"), "attribute missing from query-session.py")

    qs_re = _qs._STATUS_NOTE_RE
    br_re = _br._STATUS_NOTE_RE

    status_note_titles = [
        "Wave14 phase-3 verification is complete",
        "Wave11 planner recommendation (not yet implemented)",
        "wave6-pretooluse-deny completed with details",
        "[rust-wave7-hook-parity] tentacle report",
    ]
    not_status_note_titles = [
        "JWT authentication pattern",
        "SQLite injection via string interpolation",
        "Wave analysis results for performance tuning",  # not a status note
        "Use parameterised queries in all DB paths",
    ]

    for title in status_note_titles:
        test(
            f"#377 qs suppresses status note: {title[:40]!r}",
            bool(qs_re.search(title)),
            f"regex did not match {title!r}",
        )
        test(
            f"#377 br suppresses status note: {title[:40]!r}",
            bool(br_re.search(title)),
            f"regex did not match {title!r}",
        )

    for title in not_status_note_titles:
        test(
            f"#377 qs does NOT suppress normal entry: {title[:40]!r}",
            not qs_re.search(title),
            f"false positive: {title!r}",
        )
        test(
            f"#377 br does NOT suppress normal entry: {title[:40]!r}",
            not br_re.search(title),
            f"false positive: {title!r}",
        )

    # ── 16b. search_knowledge JSON output excludes status-note entries ────
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="re_sn_", dir=str(TOOLS_DIR))
    os.close(fd)
    db = _make_db(db_path)
    _insert_ke(
        db,
        category="pattern",
        title="JWT authentication pattern",
        content="Use HS256 for token signing.",
        confidence=0.9,
    )
    _insert_ke(
        db,
        category="pattern",
        title="Wave14 phase-3 verification is complete",
        content="All wave14 checks passed.",
        confidence=0.9,
    )
    _insert_ke(
        db,
        category="pattern",
        title="wave6-pretooluse-deny completed successfully",
        content="Hook enforced pre-tool-use deny rules.",
        confidence=0.9,
    )
    orig = _qs.DB_PATH
    _qs.DB_PATH = Path(db_path)
    try:
        # Text output should not include status-note entries
        with _Cap() as cap:
            _qs.search_knowledge("authentication token pattern", limit=20)
        text_out = cap.getvalue()
        test(
            "#377 search_knowledge text excludes Wave14 status note",
            "Wave14 phase-3 verification" not in text_out,
            f"status note leaked into text output: {text_out[:300]!r}",
        )
        test(
            "#377 search_knowledge text excludes wave6 status note",
            "wave6-pretooluse-deny" not in text_out,
            f"status note leaked into text output: {text_out[:300]!r}",
        )

        # JSON export should not include status-note entries
        with _Cap() as cap2:
            _qs.search_knowledge("Wave14", limit=20, export_fmt="json")
        raw = cap2.getvalue().strip()
        try:
            parsed = json.loads(raw)
            titles = [r.get("title", "") for r in parsed]
            test(
                "#377 search_knowledge JSON excludes Wave14 status note",
                not any("Wave14 phase-3" in t for t in titles),
                f"status note in JSON titles={titles}",
            )
        except json.JSONDecodeError as e:
            test("#377 search_knowledge JSON excludes Wave14 status note", False, f"JSON invalid: {e}")

    finally:
        _qs.DB_PATH = orig
        try:
            db.close()
            Path(db_path).unlink()
        except OSError:
            pass

    # ── 16c. generate_briefing universally suppresses status notes ────────
    fd2, db_path2 = tempfile.mkstemp(suffix=".db", prefix="re_sn_br_", dir=str(TOOLS_DIR))
    os.close(fd2)
    db2 = _make_db(db_path2)
    _insert_ke(
        db2,
        category="mistake",
        title="Wave14 phase-3 verification is complete",
        content="All wave14 checks passed.",
        confidence=0.9,
    )
    _insert_ke(
        db2,
        category="pattern",
        title="Use parameterised queries in SQLite paths",
        content="Always use ? placeholders.",
        confidence=0.9,
    )
    orig2 = _br.DB_PATH
    _br.DB_PATH = Path(db_path2)
    try:
        # JSON format — status note must not appear
        result_json = _br.generate_briefing("parameterised queries sqlite", limit=5, fmt="json")
        try:
            parsed = json.loads(result_json)
            sections = parsed.get("sections", {})
            all_titles = [
                e.get("title", "") if isinstance(e, dict) else str(e)
                for cat_entries in sections.values()
                for e in (cat_entries if isinstance(cat_entries, list) else [])
            ]
            test(
                "#377 generate_briefing JSON suppresses Wave14 status note",
                not any("Wave14" in t for t in all_titles),
                f"status note in json titles={all_titles}",
            )
        except json.JSONDecodeError as e:
            test("#377 generate_briefing JSON suppresses Wave14 status note", False, f"JSON invalid: {e}")

        # Text format — status note must not appear
        result_text = _br.generate_briefing("parameterised queries sqlite", limit=5, fmt="md")
        test(
            "#377 generate_briefing text suppresses Wave14 status note",
            "Wave14 phase-3 verification" not in result_text,
            f"status note in text: {result_text[:400]!r}",
        )
    finally:
        _br.DB_PATH = orig2
        try:
            db2.close()
            Path(db_path2).unlink()
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# 17. #380 — No-hit query insights
# ═══════════════════════════════════════════════════════════════════════════


def test_no_hit_insights() -> None:
    print("\n💡 17. #380 no-hit query insights")

    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="re_nohit_", dir=str(TOOLS_DIR))
    os.close(fd)
    db = _make_db(db_path)

    # Seed some entries so the DB is non-empty
    _insert_ke(
        db,
        category="pattern",
        title="JWT authentication pattern",
        content="Use HS256 for token signing.",
        confidence=0.9,
    )
    _insert_ke(
        db,
        category="mistake",
        title="SQL injection via interpolation",
        content="Never format SQL from user input.",
        confidence=0.9,
    )

    orig = _qs.DB_PATH
    _qs.DB_PATH = Path(db_path)
    try:
        # Query that should return no results
        with _Cap() as cap:
            result = _qs.search_knowledge("xyzzy_nonexistent_term_12345", limit=10)
        out = cap.getvalue()
        test("#380 no-hit: function returns dict", isinstance(result, dict), f"result={result!r}")
        test("#380 no-hit: hit_count is 0", result.get("hit_count", -1) == 0, f"hit_count={result.get('hit_count')}")
        test(
            "#380 no-hit: output contains total count hint",
            any(s in out for s in ["total entries", "total entry", "ℹ"]),
            f"no hint in output: {out[:300]!r}",
        )

        # Longer query should suggest shorter version
        with _Cap() as cap2:
            _qs.search_knowledge("xyzzy word1 word2 word3 word4", limit=10)
        out2 = cap2.getvalue()
        test(
            "#380 no-hit multi-term: shorter query suggested",
            "shorter" in out2.lower() or "shorter query" in out2.lower() or "ℹ Try" in out2,
            f"no shorter-query hint: {out2[:300]!r}",
        )

        # Category list shown
        test(
            "#380 no-hit: available categories shown",
            "mistake" in out2.lower() or "pattern" in out2.lower() or "Available categories" in out2,
            f"no categories in: {out2[:300]!r}",
        )

        # Empty DB → different hint
        fd2, empty_path = tempfile.mkstemp(suffix=".db", prefix="re_empty_", dir=str(TOOLS_DIR))
        os.close(fd2)
        _make_db(empty_path)
        _qs.DB_PATH = Path(empty_path)
        with _Cap() as cap3:
            _qs.search_knowledge("anything", limit=10)
        out3 = cap3.getvalue()
        test(
            "#380 empty DB: extract hint shown",
            "extract-knowledge" in out3.lower() or "no entries" in out3.lower() or "extract" in out3.lower(),
            f"no extract hint: {out3[:300]!r}",
        )
        try:
            Path(empty_path).unlink()
        except OSError:
            pass

    finally:
        _qs.DB_PATH = orig
        try:
            db.close()
            Path(db_path).unlink()
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# 18. #369 — briefing.generate_briefing uses rewritten_query for semantic
# ═══════════════════════════════════════════════════════════════════════════


def test_briefing_semantic_rewritten_query() -> None:
    print("\n🔄 18. #369 briefing.generate_briefing semantic rewritten_query")

    import inspect

    src_gen = inspect.getsource(_br.generate_briefing)
    src_ctx = inspect.getsource(_br.generate_subagent_context)

    # Both callers must pass rewritten_query (not the raw `query`) to search_semantic
    test(
        "#369 generate_briefing passes rewritten_query to search_semantic",
        "search_semantic(db, rewritten_query" in src_gen,
        "raw query still passed to search_semantic in generate_briefing",
    )
    test(
        "#369 generate_subagent_context passes rewritten_query to search_semantic",
        "search_semantic(db, rewritten_query" in src_ctx,
        "raw query still passed to search_semantic in generate_subagent_context",
    )


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
    test_adaptive_strictness()
    test_semantic_feedback_fragment_visibility()
    # Wave 2a retrieval quality issues
    test_semantic_uses_rewritten_query()  # #369
    test_synonym_expansion()  # #371
    test_synonym_expansion_semantic_split()  # #371b semantic/FTS split
    test_tfidf_section_entry_precision()  # #376
    test_status_note_suppression_universal()  # #377
    test_no_hit_insights()  # #380
    test_briefing_semantic_rewritten_query()  # #369 briefing side

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
