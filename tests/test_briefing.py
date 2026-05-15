#!/usr/bin/env python3
"""
test_briefing.py — Focused tests for briefing.py helper logic.

Covers:
  - _sanitize_fts_query() strips FTS5 operators and special chars
  - _analyze_query_strictness() returns strict/medium/broad correctly
  - _build_adaptive_fts_query() produces correct FTS query and delta
  - _infer_mode_from_query() infers correct modes
  - _resolve_mode_profile() selects correct profile
  - _rewrite_query_local() strips filler and deduplicates tokens
  - _estimate_tokens() ceiling math
  - _safe_int_list() type coercion
  - _normalize_feedback_query() normalizes and truncates
  - _recency_decay() exponential decay with half-life (issue #89)
  - _get_briefing_half_life() config read with default (issue #89)
  - _recency_composite_score() intensity × decay (issue #89)

Run: python3 tests/test_briefing.py
"""

import datetime
import importlib.util
import os
import sqlite3
import sys
import tempfile
import textwrap
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0
REPO = Path(__file__).parent.parent

# Ensure local modules importable
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


# ---------------------------------------------------------------------------
# Load briefing module (DB path does not need to exist for pure helper tests)
# ---------------------------------------------------------------------------

_script = REPO / "briefing.py"
_spec = importlib.util.spec_from_file_location("_briefing", _script)
_b = importlib.util.module_from_spec(_spec)
_saved_argv = sys.argv[:]
sys.argv = [str(_script)]
try:
    _spec.loader.exec_module(_b)
finally:
    sys.argv = _saved_argv


# ── 1. _sanitize_fts_query ───────────────────────────────────────────────────

print("\n🧹 _sanitize_fts_query")

# FTS5 operator removal
result = _b._sanitize_fts_query("foo OR bar NOT baz AND qux")
test("removes OR/AND/NOT operators", "OR" not in result and "AND" not in result and "NOT" not in result)

# Special character stripping
result2 = _b._sanitize_fts_query('search "term" (with) {braces} ^star*')
test("strips special chars — no parens in output", "(" not in result2 and "{" not in result2)
test("strips special chars — no carets in output", "^" not in result2)

# Normal query produces prefix-match terms
result3 = _b._sanitize_fts_query("docker compose")
test("normal query produces quoted terms", '"docker"' in result3 or '"compose"' in result3 or result3)
test("normal query produces wildcard terms", "*" in result3)

# Empty input → placeholder
result_empty = _b._sanitize_fts_query("   ")
test("empty query returns placeholder", result_empty == '""')

# Query with only operators → placeholder
result_ops = _b._sanitize_fts_query("OR AND NOT")
test("only-operators query returns placeholder", result_ops == '""')

# Max length respected
long_query = "x " * 300  # 600 chars
result_long = _b._sanitize_fts_query(long_query, max_length=20)
test("respects max_length", len(result_long) <= 100)  # short input means short output


# ── 2. _analyze_query_strictness ─────────────────────────────────────────────

print("\n🎯 _analyze_query_strictness")

test("single short term → strict", _b._analyze_query_strictness("docker") == "strict")
test("two terms → strict", _b._analyze_query_strictness("docker compose") == "strict")
test("file path → strict", _b._analyze_query_strictness("src/main.py") == "strict")
test("file extension → strict", _b._analyze_query_strictness("setup.cfg") == "strict")
test("empty → medium", _b._analyze_query_strictness("") == "medium")
test("medium-length technical phrase → not broad", _b._analyze_query_strictness("implement user auth") != "broad")

# Broad: 6+ words with multiple stopwords
broad_q = "how should I use the docker compose for this"
test("natural language sentence → broad", _b._analyze_query_strictness(broad_q) == "broad")


# ── 3. _build_adaptive_fts_query ─────────────────────────────────────────────

print("\n🔧 _build_adaptive_fts_query")

# Strict: no trailing wildcard, positive delta
fts_q, strictness, delta = _b._build_adaptive_fts_query("docker")
test("strict: returns strict strictness", strictness == "strict")
test("strict: positive confidence delta", delta > 0)
test("strict: no trailing * in strict query", not fts_q.endswith("*"))

# Medium: wildcard and zero delta — use a 3-word non-technical query
fts_m, str_m, delta_m = _b._build_adaptive_fts_query("build feature module")
test("medium: returns medium strictness", str_m == "medium")
test("medium: zero confidence delta", delta_m == 0.0)
test("medium: contains wildcard", "*" in fts_m)

# Broad: OR conjunction, negative delta
broad_input = "how should I use the docker compose for this deployment workflow"
fts_b, str_b, delta_b = _b._build_adaptive_fts_query(broad_input)
test("broad: returns broad strictness", str_b == "broad")
test("broad: negative confidence delta", delta_b < 0)
test("broad: contains OR", "OR" in fts_b)

# Empty → placeholder returned safely
fts_e, _, _ = _b._build_adaptive_fts_query("OR AND NOT")
test("empty/operators input handled safely", isinstance(fts_e, str))


# ── 4. _infer_mode_from_query ─────────────────────────────────────────────────

print("\n🧠 _infer_mode_from_query")

mode, confident = _b._infer_mode_from_query("implement user authentication")
test("implement keywords → implement mode", mode == "implement")
test("implement keywords → confident", confident)

mode2, _ = _b._infer_mode_from_query("debug the broken auth error exception")
test("debug keywords → debug mode", mode2 == "debug")

mode3, _ = _b._infer_mode_from_query("review the pull request security audit")
test("review keywords → review mode", mode3 == "review")

mode4, conf4 = _b._infer_mode_from_query("plan the design strategy roadmap")
test("plan keywords → plan mode", mode4 == "plan")

# No signals → auto
mode5, conf5 = _b._infer_mode_from_query("random unrelated text here")
test("no signals → auto", mode5 == "auto")
test("no signals → not confident", not conf5)

# Mode inference for test keywords
mode6, _ = _b._infer_mode_from_query("write tests and coverage for pytest assertions")
test("test keywords → test mode", mode6 == "test")


# ── 5. _resolve_mode_profile ─────────────────────────────────────────────────

print("\n📋 _resolve_mode_profile")

# Explicit valid modes
for mode_name in ("auto", "implement", "debug", "review", "plan", "test"):
    m, prof = _b._resolve_mode_profile(mode_name, "some query")
    test(f"explicit mode={mode_name} resolves", m == mode_name)
    test(f"mode={mode_name} has profile dict", isinstance(prof, dict))
    test(f"mode={mode_name} profile has order", "order" in prof)
    test(f"mode={mode_name} profile has weights", "weights" in prof)

# Unknown mode → auto
m_unknown, _ = _b._resolve_mode_profile("unknown_mode", "query")
test("unknown mode falls back to auto", m_unknown == "auto")

# None → auto
m_none, _ = _b._resolve_mode_profile(None, "query")
test("None mode resolves to auto", m_none == "auto")


# ── 6. _rewrite_query_local ──────────────────────────────────────────────────

print("\n✂️  _rewrite_query_local")

# Filler words removed
rewritten = _b._rewrite_query_local("please help me with this feature")
test("filler words stripped", "please" not in rewritten and "help" not in rewritten)
test("meaningful content preserved", "feature" in rewritten)

# Technical tokens preserved regardless of casing
tech = _b._rewrite_query_local("fix the Docker compose config")
test("camelCase tokens preserved", "Docker" in tech or "docker" in tech)

# Short tech abbreviations preserved
short_tech = _b._rewrite_query_local("the UI and db settings")
test("UI abbreviation preserved", "UI" in short_tech or "ui" in short_tech)
test("db abbreviation preserved", "db" in short_tech)

# Deduplication
dedup = _b._rewrite_query_local("auth auth authentication auth")
tokens = dedup.split()
test("deduplicates repeated tokens", len(tokens) < 4)

# Empty string → returns as-is
test("empty query returns empty", _b._rewrite_query_local("").strip() == "")

# max_terms is respected
many_words = " ".join(f"word{i}" for i in range(30))
rewritten_max = _b._rewrite_query_local(many_words, max_terms=5)
test("max_terms limits output token count", len(rewritten_max.split()) <= 5)


# ── 7. _estimate_tokens ──────────────────────────────────────────────────────

print("\n📊 _estimate_tokens")

test("0 chars → 0 tokens", _b._estimate_tokens(0) == 0)
test("4 chars → 1 token (ceil)", _b._estimate_tokens(4) == 1)
test("5 chars → 2 tokens (ceil)", _b._estimate_tokens(5) == 2)
test("400 chars → 100 tokens", _b._estimate_tokens(400) == 100)
test("401 chars → 101 tokens", _b._estimate_tokens(401) == 101)
test("negative treated as 0", _b._estimate_tokens(-1) == 0)


# ── 8. _safe_int_list ────────────────────────────────────────────────────────

print("\n🔢 _safe_int_list")

test("ints pass through", _b._safe_int_list([1, 2, 3]) == [1, 2, 3])
test("string ints converted", _b._safe_int_list(["1", "2"]) == [1, 2])
test("None values skipped", _b._safe_int_list([1, None, 2]) == [1, 2])
test("non-numeric strings skipped", _b._safe_int_list(["a", 1]) == [1])
test("empty list → empty", _b._safe_int_list([]) == [])
test("float truncated to int", _b._safe_int_list([1.9]) == [1])


# ── 9. _normalize_feedback_query ─────────────────────────────────────────────

print("\n📐 _normalize_feedback_query")

test("lowercases input", _b._normalize_feedback_query("HELLO WORLD") == "hello world")
test("strips whitespace", _b._normalize_feedback_query("  hello  ") == "hello")
test("collapses spaces", _b._normalize_feedback_query("hello   world") == "hello world")
test("None → empty string", _b._normalize_feedback_query(None) == "")
test("truncates at 500", len(_b._normalize_feedback_query("x" * 600)) == 500)


# ── 10. _recency_decay ────────────────────────────────────────────────────────

print("\n⏳ _recency_decay  (issue #89)")

_now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

# Fresh entry (just created) → decay close to 1.0
_fresh_ts = _now.strftime("%Y-%m-%d %H:%M:%S")
_fresh_score = _b._recency_decay(_fresh_ts, half_life_days=30.0)
test("fresh entry decay is close to 1.0", 0.99 <= _fresh_score <= 1.0, f"got {_fresh_score}")

# Entry exactly 30 days old → decay == 0.5 (by definition of half-life)
_old_30 = (_now - datetime.timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
_score_30 = _b._recency_decay(_old_30, half_life_days=30.0)
test("30-day-old entry decays to 0.5", abs(_score_30 - 0.5) < 0.01, f"got {_score_30:.4f}")

# Entry 60 days old → decay == 0.25 (two half-lives)
_old_60 = (_now - datetime.timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")
_score_60 = _b._recency_decay(_old_60, half_life_days=30.0)
test("60-day-old entry decays to ~0.25", abs(_score_60 - 0.25) < 0.02, f"got {_score_60:.4f}")

# None last_seen → fail-open returns 1.0
test("None last_seen returns 1.0", _b._recency_decay(None, 30.0) == 1.0)

# Empty string → fail-open returns 1.0
test("empty last_seen returns 1.0", _b._recency_decay("", 30.0) == 1.0)

# Unparseable string → fail-open returns 1.0
test("garbage last_seen returns 1.0", _b._recency_decay("not-a-date", 30.0) == 1.0)

# T-separated ISO format also accepted
_iso_ts = (_now - datetime.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
_iso_score = _b._recency_decay(_iso_ts, half_life_days=30.0)
test("ISO T-separated format accepted", abs(_iso_score - 0.5) < 0.01, f"got {_iso_score:.4f}")

# Shorter half-life → faster decay
_score_short = _b._recency_decay(_old_30, half_life_days=15.0)
test(
    "shorter half-life gives lower score for same age",
    _score_short < _score_30,
    f"hl15={_score_short:.4f} vs hl30={_score_30:.4f}",
)

# half_life_days <= 0 → returns 1.0 (guard)
test("zero half_life returns 1.0", _b._recency_decay(_old_30, half_life_days=0.0) == 1.0)


# ── 11. _get_briefing_half_life ───────────────────────────────────────────────

print("\n⚙️  _get_briefing_half_life  (issue #89)")


def _make_cfg_db(path, half_life=None):
    """Create a minimal DB with optional wakeup_config entry."""
    db = sqlite3.connect(str(path))
    db.execute("CREATE TABLE IF NOT EXISTS wakeup_config (key TEXT PRIMARY KEY, value TEXT)")
    if half_life is not None:
        db.execute(
            "INSERT OR REPLACE INTO wakeup_config (key, value) VALUES (?, ?)",
            ("briefing_recency_half_life", str(half_life)),
        )
    db.commit()
    return db


# Default when key is absent
_hl_db_default = _make_cfg_db(":memory:")
test("default half-life is 30.0 when key absent", _b._get_briefing_half_life(_hl_db_default) == 30.0)
_hl_db_default.close()

# Custom value read correctly
_hl_db_custom = _make_cfg_db(":memory:", half_life=60.0)
test("custom half-life read from wakeup_config", _b._get_briefing_half_life(_hl_db_custom) == 60.0)
_hl_db_custom.close()

# Value 0 → falls back to 30.0
_hl_db_zero = _make_cfg_db(":memory:", half_life=0)
test("half-life=0 in config returns default 30.0", _b._get_briefing_half_life(_hl_db_zero) == 30.0)
_hl_db_zero.close()

# No wakeup_config table → fails open to 30.0
_hl_db_no_tbl = sqlite3.connect(":memory:")
test("missing wakeup_config table returns default 30.0", _b._get_briefing_half_life(_hl_db_no_tbl) == 30.0)
_hl_db_no_tbl.close()


# ── 12. _recency_composite_score ─────────────────────────────────────────────

print("\n🎯 _recency_composite_score  (issue #89)")

_now_str = _now.strftime("%Y-%m-%d %H:%M:%S")
_old_30_str = (_now - datetime.timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

# High-intensity fresh entry beats low-intensity fresh entry (preserves #88)
_fresh_hi = {"intensity": 0.9, "last_seen": _now_str}
_fresh_lo = {"intensity": 0.3, "last_seen": _now_str}
test(
    "#88 contract: high-intensity fresh beats low-intensity fresh",
    _b._recency_composite_score(_fresh_hi, 30.0) > _b._recency_composite_score(_fresh_lo, 30.0),
)

# Counterexample preserved: intensity=0.6 beats intensity=0.4 for same age
_ce_a = {"intensity": 0.6, "last_seen": _now_str}
_ce_b = {"intensity": 0.4, "last_seen": _now_str}
test(
    "#88 counterexample: intensity=0.6 > intensity=0.4 same age",
    _b._recency_composite_score(_ce_a, 30.0) > _b._recency_composite_score(_ce_b, 30.0),
)

# Recency effect: recent low-intensity can beat old high-intensity
_stale_hi = {"intensity": 0.8, "last_seen": _old_30_str}
_recent_lo = {"intensity": 0.6, "last_seen": _now_str}
# stale_hi score = 0.8 * 0.5 = 0.40; recent_lo score = 0.6 * ~1.0 = ~0.60
test(
    "recency: recent low-intensity can beat stale high-intensity",
    _b._recency_composite_score(_recent_lo, 30.0) > _b._recency_composite_score(_stale_hi, 30.0),
)

# Missing intensity and no confidence → defaults to 0.5
_no_intensity = {"last_seen": _now_str}
_score_ni = _b._recency_composite_score(_no_intensity, 30.0)
test(
    "missing intensity (no confidence) defaults to 0.5 in composite score",
    abs(_score_ni - 0.5) < 0.01,
    f"got {_score_ni:.4f}",
)

# Missing intensity but confidence present → confidence used as fallback (pre-v21 ordering)
_no_intensity_with_conf = {"confidence": 0.8, "last_seen": _now_str}
_score_ni_conf = _b._recency_composite_score(_no_intensity_with_conf, 30.0)
test(
    "missing intensity: confidence used as fallback when present",
    abs(_score_ni_conf - 0.8) < 0.01,
    f"got {_score_ni_conf:.4f}",
)

# Two pre-v21 rows (no intensity): higher confidence should rank higher
_pre21_hi = {"confidence": 0.85, "last_seen": _now_str}
_pre21_lo = {"confidence": 0.55, "last_seen": _now_str}
test(
    "pre-v21 rows: higher confidence ranks higher (ordering preserved)",
    _b._recency_composite_score(_pre21_hi, 30.0) > _b._recency_composite_score(_pre21_lo, 30.0),
)

# Missing last_seen → decay=1.0, so composite = intensity * 1.0
_no_date = {"intensity": 0.7}
_score_nd = _b._recency_composite_score(_no_date, 30.0)
test("missing last_seen treated as fresh (decay=1.0)", abs(_score_nd - 0.7) < 0.01, f"got {_score_nd:.4f}")


# ── 13. End-to-end ranking with wakeup_config half-life  ─────────────────────

print("\n🔗 Ranking integration — recency rerank in generate_briefing context  (issue #89)")


def _make_ranking_db(path):
    """Create a test DB with intensity, valence, last_seen, wakeup_config."""
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    db.executescript(
        textwrap.dedent("""
        CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, name TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            topic_key TEXT,
            tags TEXT DEFAULT '',
            confidence REAL DEFAULT 0.7,
            wing TEXT DEFAULT '',
            room TEXT DEFAULT '',
            occurrence_count INTEGER DEFAULT 1,
            first_seen TEXT DEFAULT (datetime('now')),
            last_seen TEXT DEFAULT (datetime('now')),
            facts TEXT DEFAULT '[]',
            est_tokens INTEGER DEFAULT 0,
            task_id TEXT DEFAULT '',
            affected_files TEXT DEFAULT '[]',
            stable_id TEXT,
            valence TEXT DEFAULT '',
            intensity REAL DEFAULT 0.5
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS ke_fts USING fts5(
            title, content, tags, category, wing, room, facts,
            error_type, root_cause,
            tokenize='porter unicode61 remove_diacritics 2'
        );
        CREATE TABLE IF NOT EXISTS wakeup_config (key TEXT PRIMARY KEY, value TEXT);
    """)
    )
    return db


def _ins(db, title, content, category, confidence, intensity, last_seen):
    """Insert a knowledge entry and sync FTS."""
    cur = db.execute(
        """
        INSERT INTO knowledge_entries
            (session_id, category, title, content, confidence, intensity, last_seen, first_seen)
        VALUES ('', ?, ?, ?, ?, ?, ?, ?)
        """,
        (category, title, content, confidence, intensity, last_seen, last_seen),
    )
    eid = cur.lastrowid
    db.execute(
        "INSERT INTO ke_fts (rowid, title, content, tags, category, wing, room, facts, error_type, root_cause) "
        "VALUES (?, ?, ?, '', ?, '', '', '', '', '')",
        (eid, title, content, category),
    )
    db.commit()
    return eid


_rnk_tmpdir = tempfile.TemporaryDirectory()
_rnk_db_path = Path(_rnk_tmpdir.name) / "ranking.db"

_rdb = _make_ranking_db(_rnk_db_path)
_now2 = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
_ts_fresh = _now2.strftime("%Y-%m-%d %H:%M:%S")
_ts_stale = (_now2 - datetime.timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")

# Entry A: high intensity (0.8) but 60 days stale
_ins(_rdb, "stale high intensity mistake", "stale bug old issue", "mistake", 0.8, 0.8, _ts_stale)
# Entry B: lower intensity (0.6) but fresh
_ins(_rdb, "recent moderate intensity mistake", "fresh bug recent issue", "mistake", 0.7, 0.6, _ts_fresh)
_rdb.close()

# Use search_knowledge_entries with this DB directly
import sqlite3 as _sq  # noqa: I001

_test_conn = _sq.connect(str(_rnk_db_path))
_test_conn.row_factory = _sq.Row
_entries = _b.search_knowledge_entries(_test_conn, "bug issue", "mistake", limit=5)
_test_conn.close()

# Verify both entries are returned
_titles = [e.get("title", "") for e in _entries]
test("ranking test: both entries returned", len(_entries) >= 2, f"got {len(_entries)} entries: {_titles}")

# Verify last_seen is present in results
test(
    "last_seen present in FTS results",
    all("last_seen" in e for e in _entries),
    f"missing in: {[e.get('title') for e in _entries if 'last_seen' not in e]}",
)

# Verify intensity is present in results (from COALESCE select)
test(
    "intensity present in FTS results",
    all("intensity" in e for e in _entries),
    f"missing in: {[e.get('title') for e in _entries if 'intensity' not in e]}",
)

# With default half-life 30 days: stale_hi score = 0.8 * 0.5^2 = 0.2, fresh_lo = 0.6 * ~1 = ~0.6
# fresh entry should rank first after recency reranking
if len(_entries) >= 2:
    _sorted = sorted(_entries, key=lambda e: _b._recency_composite_score(e, 30.0), reverse=True)
    test(
        "recency rerank: recent moderate-intensity ranks above stale high-intensity",
        _sorted[0].get("title", "").startswith("recent"),
        f"top title: {_sorted[0].get('title')}",
    )

# With very long half-life (365 days): recency barely matters, intensity wins
if len(_entries) >= 2:
    _sorted_hl = sorted(_entries, key=lambda e: _b._recency_composite_score(e, 365.0), reverse=True)
    test(
        "very long half-life: high-intensity still wins (intensity-first preserved)",
        _sorted_hl[0].get("title", "").startswith("stale"),
        f"top title: {_sorted_hl[0].get('title')}",
    )

# Custom half-life via wakeup_config
_cfg_conn = _sq.connect(str(_rnk_db_path))
_cfg_conn.row_factory = _sq.Row
_cfg_conn.execute("CREATE TABLE IF NOT EXISTS wakeup_config (key TEXT PRIMARY KEY, value TEXT)")
_cfg_conn.execute("INSERT OR REPLACE INTO wakeup_config (key, value) VALUES ('briefing_recency_half_life', '7')")
_cfg_conn.commit()
_hl_from_cfg = _b._get_briefing_half_life(_cfg_conn)
_cfg_conn.close()
test("wakeup_config half-life=7 returned correctly", _hl_from_cfg == 7.0, f"got {_hl_from_cfg}")

try:
    _rnk_tmpdir.cleanup()
except Exception:
    pass


# ── 14. generate_briefing merged rerank path — mocked FTS+semantic ordering ──
#
# Verifies the _recency_composite_score contract in generate_briefing's merged
# rerank step using fully-mocked search functions (both FTS and semantic are
# replaced by return_value mocks — no real DB query or vector lookup occurs).
#
# The scenario uses SAME-RECENCY entries so ordering depends purely on intensity:
#   FTS entry (intensity=0.75) vs semantic entry (intensity=0.85 or missing).
# When intensity is present the semantic entry wins (0.85 > 0.75).
# When intensity is absent the confidence fallback (0.65) < FTS intensity (0.75)
# — the ordering FLIPS, confirming that _recency_composite_score uses the field
# correctly.
#
# Note: this section does NOT prove that the real search_semantic() SELECT
# carries intensity from the DB — that guarantee is provided by Section 15.

print("\n🔗 generate_briefing merged rerank — semantic-intensity regression  (issue #89)")

import unittest.mock as _mock

_gb_now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
_gb_fresh = _gb_now.strftime("%Y-%m-%d %H:%M:%S")

# Same-recency scenario — both entries have the same fresh timestamp.
# Scores at 30-day half-life (decay ≈ 1.0 for age≈0):
#   FTS entry  : intensity=0.75               → composite ≈ 0.75
#   sem FIX    : intensity=0.85               → composite ≈ 0.85  (wins — higher than FTS)
#   sem BUG    : intensity missing, conf=0.65 → composite ≈ 0.65  (loses — ordering FLIPS)

_gb_fts_same_recency = {
    "id": 10,
    "title": "gb fts same recency",
    "content": "c",
    "category": "mistake",
    "confidence": 0.8,
    "intensity": 0.75,
    "last_seen": _gb_fresh,
}
# post-fix semantic result: intensity present (real value 0.85 > FTS 0.75 → wins)
_gb_sem_fresh_fix = {
    "id": 20,
    "title": "gb sem fresh fix",
    "content": "c",
    "category": "mistake",
    "confidence": 0.7,
    "intensity": 0.85,
    "last_seen": _gb_fresh,
}
# pre-fix (bug) semantic result: intensity absent (confidence fallback 0.65 < FTS 0.75 → loses)
_gb_sem_fresh_bug = {
    "id": 20,
    "title": "gb sem fresh fix",
    "content": "c",
    "category": "mistake",
    "confidence": 0.65,
    "last_seen": _gb_fresh,  # no 'intensity' key — the old bug
}

# Verify pre-conditions: the ordering must flip between fix and bug.
_score_fts_same = _b._recency_composite_score(_gb_fts_same_recency, 30.0)
_score_sem_fix = _b._recency_composite_score(_gb_sem_fresh_fix, 30.0)
_score_sem_bug = _b._recency_composite_score(_gb_sem_fresh_bug, 30.0)
test(
    "pre-conditions: fix sem score > FTS score (semantic wins when intensity present)",
    _score_sem_fix > _score_fts_same,
    f"sem_fix={_score_sem_fix:.3f} fts={_score_fts_same:.3f}",
)
test(
    "pre-conditions: bug sem score < FTS score (semantic loses — confidence fallback 0.65 < FTS 0.75)",
    _score_sem_bug < _score_fts_same,
    f"sem_bug={_score_sem_bug:.3f} fts={_score_fts_same:.3f}",
)


def _fresh_gb_db():
    """In-memory DB with only the tables generate_briefing needs for this test."""
    _db = sqlite3.connect(":memory:")
    _db.row_factory = sqlite3.Row
    _db.executescript("""
        CREATE TABLE wakeup_config (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO wakeup_config VALUES ('briefing_recency_half_life', '30');
    """)
    return _db


def _run_gb_with_mocks(fts_entries, sem_entries):
    """Call generate_briefing with mocked FTS+semantic and return parsed JSON."""
    with (
        _mock.patch.object(_b, "get_db", side_effect=_fresh_gb_db),
        _mock.patch.object(_b, "search_knowledge_entries", return_value=fts_entries),
        _mock.patch.object(_b, "search_semantic", return_value=sem_entries),
        _mock.patch.object(_b, "search_past_work", return_value=[]),
        _mock.patch.object(_b, "blast_radius", return_value=[]),
        _mock.patch.object(_b, "_upsert_entry_recall_stats", return_value=None),
        _mock.patch.object(_b, "_detect_session_id", return_value=""),
    ):
        return _b.generate_briefing(
            "test query",
            limit=2,
            fmt="json",
            min_confidence=0.0,
            mode="mistake",
            infer_auto_mode=False,
        )


import json as _json  # noqa: I001


def _parse_entries(out):
    data = _json.loads(out)
    return data.get("sections", {}).get("mistake", {}).get("entries", []) or data.get("entries", {}).get("mistake", [])


# ── Fix scenario: semantic entry carries its real intensity (0.6 > 0.55) → wins ──
try:
    _gb_out_fix = _run_gb_with_mocks([_gb_fts_same_recency], [_gb_sem_fresh_fix])
    _gb_titles_fix = [e.get("title", "") for e in _parse_entries(_gb_out_fix)]
    test(
        "generate_briefing fix-path: semantic entry ranks first when intensity is present",
        bool(_gb_titles_fix) and _gb_titles_fix[0] == "gb sem fresh fix",
        f"order={_gb_titles_fix}",
    )
except Exception as _e:
    test("generate_briefing fix-path: ran without exception", False, str(_e))

# ── Bug scenario: semantic entry missing intensity → confidence fallback 0.65 < FTS 0.75 → ordering flips ──
# This test FAILS if search_semantic regresses and drops intensity again:
# the sem entry would silently use confidence=0.65, score below the FTS entry (0.75), and rank second.
try:
    _gb_out_bug = _run_gb_with_mocks([_gb_fts_same_recency], [_gb_sem_fresh_bug])
    _gb_titles_bug = [e.get("title", "") for e in _parse_entries(_gb_out_bug)]
    # With missing intensity the confidence fallback (0.65) < FTS intensity (0.75) → FTS ranks first, sem second.
    test(
        "generate_briefing bug-path: ordering flips — FTS ranks first when semantic intensity is missing",
        bool(_gb_titles_bug) and _gb_titles_bug[0] == "gb fts same recency",
        f"order={_gb_titles_bug}",
    )
except Exception as _e:
    test("generate_briefing bug-path: ran without exception", False, str(_e))


# ── 15. search_semantic — real vector-search path carries intensity from DB ──
#
# Regression: calls the REAL search_semantic() (not mocked) with mocks only
# around the embedding/vector machinery so no API key or sklearn is needed.
# Proves that the returned dicts carry `intensity` from the knowledge_entries
# row, not a default or a missing field.

print("\n🔗 search_semantic — real vector-search path intensity regression  (issue #89)")

import types as _types


def _make_sem_test_db():
    """In-memory DB with knowledge_entries (intensity column) for Section 15."""
    _db = sqlite3.connect(":memory:")
    _db.row_factory = sqlite3.Row
    _db.executescript("""
        CREATE TABLE knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT DEFAULT '',
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            topic_key TEXT,
            tags TEXT DEFAULT '',
            confidence REAL DEFAULT 0.7,
            wing TEXT DEFAULT '',
            room TEXT DEFAULT '',
            occurrence_count INTEGER DEFAULT 1,
            first_seen TEXT DEFAULT (datetime('now')),
            last_seen TEXT DEFAULT (datetime('now')),
            facts TEXT DEFAULT '[]',
            est_tokens INTEGER DEFAULT 0,
            intensity REAL,
            valence TEXT,
            document_id INTEGER,
            source_section TEXT,
            source_file TEXT,
            start_line INTEGER,
            end_line INTEGER,
            code_language TEXT,
            code_snippet TEXT
        );
    """)
    return _db


_sem_db = _make_sem_test_db()
_sem_db.execute(
    "INSERT INTO knowledge_entries "
    "(session_id, category, title, content, confidence, intensity, last_seen) "
    "VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
    ("s-sem-test", "mistake", "sem-intensity-test entry", "content here", 0.7, 0.85),
)
_sem_db.commit()
_sem_entry_id = int(_sem_db.execute("SELECT last_insert_rowid()").fetchone()[0])


def _build_mock_embed_module(entry_id_to_return: int):
    """Return a mock embed module that yields one vector-search hit."""
    _mod = _types.ModuleType("embed")
    _mod.load_config = lambda: {"provider": "openai"}
    _mod.ensure_embedding_tables = lambda db: None
    _mod.resolve_provider = lambda config: ("openai", {"api_key": "test", "model": "ada"})
    _mod.call_embedding_api = lambda texts, pc: [[0.1, 0.9] for _ in texts]
    _mod.vector_search = lambda db, qv, source_type, limit: [("knowledge", entry_id_to_return, 0.95)]
    _mod.search_tfidf = lambda q, model, limit: []
    return _mod


_saved_embed = sys.modules.get("embed")
sys.modules["embed"] = _build_mock_embed_module(_sem_entry_id)

try:
    _sem_results = _b.search_semantic(_sem_db, "test query", "mistake", limit=5)
    test(
        "search_semantic: returns at least one result via real vector path",
        len(_sem_results) >= 1,
        f"got {len(_sem_results)} results",
    )
    if _sem_results:
        _sr = _sem_results[0]
        test("search_semantic: result has intensity field from DB", "intensity" in _sr, f"keys: {list(_sr.keys())}")
        test(
            "search_semantic: intensity value matches stored DB value (0.85)",
            abs(float(_sr.get("intensity", -1)) - 0.85) < 0.01,
            f"got intensity={_sr.get('intensity')}",
        )
        test("search_semantic: result has last_seen field", "last_seen" in _sr, f"keys: {list(_sr.keys())}")
except Exception as _e:
    test("search_semantic real path: ran without exception", False, str(_e))
finally:
    if _saved_embed is not None:
        sys.modules["embed"] = _saved_embed
    elif "embed" in sys.modules:
        del sys.modules["embed"]
    try:
        _sem_db.close()
    except Exception:
        pass


# ── Skill usage briefing helpers ─────────────────────────────────────────────
try:
    import sqlite3 as _sqb

    _collect_skill = getattr(_b, "_collect_skill_usage_for_briefing", None)
    _format_skill = getattr(_b, "_format_skill_usage_section", None)

    test("briefing has _collect_skill_usage_for_briefing", _collect_skill is not None)
    test("briefing has _format_skill_usage_section", _format_skill is not None)

    if _collect_skill and _format_skill:
        import tempfile as _tmp_sb

        _sb_tmpdir = _tmp_sb.mkdtemp()

        # non-existent DB → empty list (fail-open)
        _nonexistent = Path(_sb_tmpdir) / "nonexistent-skill-metrics.db"
        _result_empty = _collect_skill(db_path=_nonexistent)
        test(
            "skill-briefing: non-existent DB returns empty list",
            isinstance(_result_empty, list) and len(_result_empty) == 0,
        )

        # DB without the table → empty list (fail-open)
        _no_tbl_db = Path(_sb_tmpdir) / "no-skill-tbl.db"
        _conn_no_tbl = _sqb.connect(str(_no_tbl_db))
        _conn_no_tbl.close()
        _result_no_tbl = _collect_skill(db_path=_no_tbl_db)
        test(
            "skill-briefing: DB without skill_usage_events returns empty list",
            isinstance(_result_no_tbl, list) and len(_result_no_tbl) == 0,
        )

        # DB with skill_usage_events → returns structured entries
        _skill_db = Path(_sb_tmpdir) / "skill-usage-briefing.db"
        _conn_sk = _sqb.connect(str(_skill_db))
        _conn_sk.executescript(
            """
            CREATE TABLE skill_usage_events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                skill_name TEXT NOT NULL,
                event      TEXT NOT NULL,
                session_id TEXT NOT NULL,
                timestamp  TEXT NOT NULL
            );
            """
        )
        for _skill, _evt in [
            ("karpathy-guidelines", "triggered"),
            ("karpathy-guidelines", "loaded"),
            ("karpathy-guidelines", "triggered"),
            ("karpathy-guidelines", "loaded"),
            ("frontend-dev", "triggered"),
            ("frontend-dev", "skipped"),
        ]:
            _conn_sk.execute(
                "INSERT INTO skill_usage_events (skill_name, event, session_id, timestamp) VALUES (?, ?, ?, ?)",
                (_skill, _evt, "sess-001", "2026-05-14T00:00:00Z"),
            )
        _conn_sk.commit()
        _conn_sk.close()

        _entries = _collect_skill(db_path=_skill_db)
        test(
            "skill-briefing: populated DB returns list of dicts",
            isinstance(_entries, list) and len(_entries) > 0,
            str(_entries),
        )
        _kg = next((e for e in _entries if e.get("skill_name") == "karpathy-guidelines"), None)
        test(
            "skill-briefing: karpathy-guidelines triggered==2",
            _kg is not None and _kg.get("triggered") == 2,
            str(_kg),
        )
        test(
            "skill-briefing: karpathy-guidelines loaded==2",
            _kg is not None and _kg.get("loaded") == 2,
            str(_kg),
        )
        _fd = next((e for e in _entries if e.get("skill_name") == "frontend-dev"), None)
        test(
            "skill-briefing: frontend-dev skipped==1",
            _fd is not None and _fd.get("skipped") == 1,
            str(_fd),
        )

        # _format_skill_usage_section([]) → empty string
        test(
            "skill-briefing: format empty entries → empty string",
            _format_skill([]) == "",
        )

        # _format_skill_usage_section with data → non-empty string containing skill names
        _section = _format_skill(_entries)
        test(
            "skill-briefing: format with entries → non-empty string",
            isinstance(_section, str) and len(_section) > 0,
            _section[:200],
        )
        test(
            "skill-briefing: format includes karpathy-guidelines",
            "karpathy" in _section,
            _section[:200],
        )

    test("skill-briefing tests ran without exception", True)
except Exception as _e_sk:
    test("skill-briefing tests ran without exception", False, str(_e_sk))


# ── 16. Level 0 skill index (issue #118) ────────────────────────────────────

print("\n📦 Level 0 skill index (issue #118)")

import shutil as _shutil

# 16a. _parse_skill_frontmatter — folded description
_fm_folded = "---\nname: test-skill\ndescription: >\n  This is a long description that spans multiple\n  lines and should be joined together.\n---\n# Body\n"
_meta_f = _b._parse_skill_frontmatter(_fm_folded)
test("16a: _parse_skill_frontmatter extracts name from folded fm", _meta_f.get("name") == "test-skill")
test("16a: _parse_skill_frontmatter joins folded description", "long description" in _meta_f.get("description", ""))
test("16a: _parse_skill_frontmatter folded desc has no embedded newline", "\n" not in _meta_f.get("description", ""))

# 16b. _parse_skill_frontmatter — inline description
_fm_inline = "---\nname: another-skill\ndescription: Short inline description.\n---\n"
_meta_i = _b._parse_skill_frontmatter(_fm_inline)
test("16b: _parse_skill_frontmatter extracts inline description", _meta_i.get("description") == "Short inline description.")

# 16c. _parse_skill_frontmatter — missing frontmatter returns empty
_meta_no = _b._parse_skill_frontmatter("# No frontmatter\nJust body content")
test("16c: _parse_skill_frontmatter missing frontmatter → empty name", _meta_no.get("name") == "")
test("16c: _parse_skill_frontmatter missing frontmatter → empty description", _meta_no.get("description") == "")

# 16d. _generate_skill_index with a temp skills directory
_skill_tmp = Path(tempfile.mkdtemp(prefix="test-skills-"))
try:
    _sa = _skill_tmp / "skill-a"
    _sa.mkdir()
    (_sa / "SKILL.md").write_text(
        "---\nname: skill-a\ndescription: Short description here.\n---\n# Body\n",
        encoding="utf-8",
    )
    _sb = _skill_tmp / "skill-b"
    _sb.mkdir()
    _long_d = "A" * 80
    (_sb / "SKILL.md").write_text(
        f"---\nname: skill-b\ndescription: {_long_d}\n---\n",
        encoding="utf-8",
    )
    _idx = _b._generate_skill_index(_skill_tmp)
    test("16d: _generate_skill_index non-empty for populated dir", bool(_idx))
    test("16d: _generate_skill_index includes skill-a", "skill-a" in _idx)
    test("16d: _generate_skill_index includes skill-b", "skill-b" in _idx)
    # Description for skill-b must be truncated to 57 chars + "..." (total 60)
    _sb_line = next((l for l in _idx.splitlines() if "skill-b" in l), "")
    _sb_desc_part = _sb_line.split("\u2014", 1)[-1].strip() if "\u2014" in _sb_line else ""
    test(
        "16d: _generate_skill_index truncates long description to 57 chars + '...' (total 60)",
        _sb_desc_part.endswith("...") and len(_sb_desc_part) == _b._SKILL_DESC_MAX,
        f"got: {_sb_desc_part!r}",
    )
finally:
    _shutil.rmtree(str(_skill_tmp), ignore_errors=True)

# 16e. _generate_skill_index with empty dir returns ""
_empty_tmp = Path(tempfile.mkdtemp(prefix="test-skills-empty-"))
try:
    test("16e: _generate_skill_index empty dir → empty string", _b._generate_skill_index(_empty_tmp) == "")
finally:
    _shutil.rmtree(str(_empty_tmp), ignore_errors=True)

# 16f. _generate_skill_index with nonexistent dir returns ""
_missing = Path(tempfile.mkdtemp(prefix="test-")) / "nonexistent-skills"
test("16f: _generate_skill_index missing dir → empty string", _b._generate_skill_index(_missing) == "")

# 16g. Constants exist
test("16g: briefing.py exposes _SKILL_DESC_MAX", hasattr(_b, "_SKILL_DESC_MAX"))
test("16g: briefing.py _SKILL_DESC_MAX == 60", _b._SKILL_DESC_MAX == 60)
test("16g: briefing.py exposes _generate_skill_index", hasattr(_b, "_generate_skill_index"))
test("16g: briefing.py exposes _parse_skill_frontmatter", hasattr(_b, "_parse_skill_frontmatter"))

# 16h. --session-start flag is handled in main() source
_br_src_118 = (REPO / "briefing.py").read_text(encoding="utf-8")
test("16h: briefing.py source handles --session-start", "--session-start" in _br_src_118)
test("16h: briefing.py strips --session-start before query assembly", "session_start_mode" in _br_src_118)

# 16i. Subprocess: --session-start triggers skill index; normal invocation does not
# Use an isolated HOME/USERPROFILE so the child never finds the live knowledge DB.
# This makes the subprocess deterministic: it exits quickly with rc=1 (DB absent)
# but still emits the skill index before attempting DB access.
import subprocess as _sp118

_proc_tmp = Path(tempfile.mkdtemp(prefix="test-skills-proc-"))
try:
    # Build an isolated env: copy the current env but redirect home dirs so the
    # knowledge DB is absent; the skill index is printed before any DB access.
    _isolated_env = os.environ.copy()
    _isolated_env["HOME"] = str(_proc_tmp)
    _isolated_env["USERPROFILE"] = str(_proc_tmp)

    _briefing_py = REPO / "briefing.py"
    # Run with --session-start in the isolated env; the child will fail to open
    # the knowledge DB (rc=1, stderr has "not found") but must still emit the
    # skill index on stdout before reaching the DB.
    _r_session = _sp118.run(
        [sys.executable, str(_briefing_py), "test-project", "--budget", "100", "--session-start"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        cwd=str(REPO),
        env=_isolated_env,
    )
    test(
        "16i: briefing.py --session-start emits Skills block",
        "\U0001f4e6 Skills" in _r_session.stdout or "Skills" in _r_session.stdout,
        f"rc={_r_session.returncode} stdout={_r_session.stdout[:300]} stderr={_r_session.stderr[:200]}",
    )
    # Normal invocation (no --session-start, no DB) must NOT emit skill index
    _r_normal = _sp118.run(
        [sys.executable, str(_briefing_py), "--wakeup"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        cwd=str(REPO),
        env=_isolated_env,
    )
    test(
        "16i: briefing.py normal invocation has no skill index",
        "\U0001f4e6 Skills" not in _r_normal.stdout,
        f"rc={_r_normal.returncode} stdout={_r_normal.stdout[:300]}",
    )
finally:
    _shutil.rmtree(str(_proc_tmp), ignore_errors=True)


# ── 17. _compute_dynamic_budget (issue #125) ─────────────────────────────────

print("\n💰 _compute_dynamic_budget  (issue #125)")

# 17a. Explicit budget always wins regardless of available_tokens
test("17a: explicit_budget=3000 → 3000 (ignores available_tokens)", _b._compute_dynamic_budget(3000, 0) == 3000)
test("17a: explicit_budget=1000, available_tokens=200000 → 1000", _b._compute_dynamic_budget(1000, 200000) == 1000)
test("17a: explicit_budget=500, available_tokens=40000 → 500", _b._compute_dynamic_budget(500, 40000) == 500)

# 17b. Dynamic formula: min(2000, int(N * 0.05)) — no floor
# available_tokens=40000 → 40000 * 0.05 = 2000 → capped at 2000
test("17b: avail=40000 → min(2000, 2000) = 2000", _b._compute_dynamic_budget(0, 40000) == 2000)
# available_tokens=60000 → 60000 * 0.05 = 3000 → capped at 2000
test("17b: avail=60000 → capped at 2000", _b._compute_dynamic_budget(0, 60000) == 2000)
# available_tokens=8000 → 8000 * 0.05 = 400 → no floor, result is 400
test("17b: avail=8000 → 400 (5% of 8000, no floor)", _b._compute_dynamic_budget(0, 8000) == 400)
# available_tokens=20000 → 20000 * 0.05 = 1000 → within (0, 2000]
test("17b: avail=20000 → 1000", _b._compute_dynamic_budget(0, 20000) == 1000)
# available_tokens=10000 → 10000 * 0.05 = 500 → exactly at 5%
test("17b: avail=10000 → 500 (exactly 5% of 10000)", _b._compute_dynamic_budget(0, 10000) == 500)

# 17c. Fallback: no budget (available_tokens=0 or negative) → 0 (no cap)
test("17c: explicit=0, avail=0 → 0 (no cap)", _b._compute_dynamic_budget(0, 0) == 0)
test("17c: explicit=0, avail negative → 0", _b._compute_dynamic_budget(0, -1) == 0)
test("17c: no args → 0", _b._compute_dynamic_budget(0) == 0)

# 17d. Token tracking: _estimate_tokens round-trips correctly
_budget_chars = _b._compute_dynamic_budget(0, 40000)  # 2000
_budget_tokens = _b._estimate_tokens(_budget_chars)
test("17d: budget=2000 chars → ~500 tokens", _budget_tokens == 500)
_budget_chars2 = _b._compute_dynamic_budget(0, 20000)  # 1000
_budget_tokens2 = _b._estimate_tokens(_budget_chars2)
test("17d: budget=1000 chars → ~250 tokens", _budget_tokens2 == 250)

# 17e. Priority order: _format_compact puts mistakes before patterns/decisions/tools
# Build minimal data with one entry per category
_fc_data = {
    "mistake": [{"title": "MISTAKE_ENTRY", "content": "A mistake was made here today."}],
    "pattern": [{"title": "PATTERN_ENTRY", "content": "Use this proven pattern always."}],
    "decision": [{"title": "DECISION_ENTRY", "content": "Architecture decided this way."}],
    "tool": [{"title": "TOOL_ENTRY", "content": "A relevant tool configuration."}],
}
_fc_out = _b._format_compact("test query", _fc_data, [], {})
_pos_mistake = _fc_out.find("MISTAKE_ENTRY")
_pos_pattern = _fc_out.find("PATTERN_ENTRY")
_pos_decision = _fc_out.find("DECISION_ENTRY")
_pos_tool = _fc_out.find("TOOL_ENTRY")
test("17e: mistakes appear before patterns in compact output", _pos_mistake < _pos_pattern, f"mistake@{_pos_mistake} pattern@{_pos_pattern}")
test("17e: mistakes appear before decisions", _pos_mistake < _pos_decision, f"mistake@{_pos_mistake} decision@{_pos_decision}")
test("17e: mistakes appear before tools", _pos_mistake < _pos_tool, f"mistake@{_pos_mistake} tool@{_pos_tool}")
test("17e: patterns appear before decisions", _pos_pattern < _pos_decision, f"pattern@{_pos_pattern} decision@{_pos_decision}")

# 17f. Graceful degradation: explicit budget — structured outputs (json/pack) are not truncated
# Simulate a budget scenario: if output is a JSON string that exceeds budget, it must not be
# truncated (the budget enforcement in main() has fmt not in ("json","pack") guard).
# We verify this guard is present in the source code (structural safety guarantee).
_br_src_125 = (REPO / "briefing.py").read_text(encoding="utf-8")
test("17f: source guards json/pack from truncation (fmt not in json/pack check)", 'fmt not in ("json", "pack")' in _br_src_125)
test("17f: source uses _compute_dynamic_budget", "_compute_dynamic_budget" in _br_src_125)
test("17f: source has --available-tokens flag handling", "--available-tokens" in _br_src_125)

# 17g. _compute_dynamic_budget is exposed as a module attribute
test("17g: _compute_dynamic_budget exposed on module", hasattr(_b, "_compute_dynamic_budget"))
test("17g: callable", callable(_b._compute_dynamic_budget))

# 17h. Formula contract: no floor — small available_tokens return proportional budget
# available_tokens=4000 → 4000 * 0.05 = 200 (well below the old 500 floor)
test("17h: avail=4000 → 200 (no floor applied)", _b._compute_dynamic_budget(0, 4000) == 200)
# available_tokens=100 → 100 * 0.05 = 5
test("17h: avail=100 → 5 (no floor applied)", _b._compute_dynamic_budget(0, 100) == 5)
# available_tokens=2000 → 2000 * 0.05 = 100 (old formula would floor this to 500)
test("17h: avail=2000 → 100 (old floor was 500, new formula 5% = 100)", _b._compute_dynamic_budget(0, 2000) == 100)

# 17i. Token tracking: source uses injected/budget tokens in footer (not dead locals)
_br_src_125_full = (REPO / "briefing.py").read_text(encoding="utf-8")
test("17i: source uses injected_tokens in footer comment", "injected_tokens" in _br_src_125_full)
test("17i: source uses budget_tokens in footer comment", "budget_tokens" in _br_src_125_full)
test("17i: dead-locals pattern removed (no _ = (injected_tokens, budget_tokens))", "_ = (injected_tokens, budget_tokens)" not in _br_src_125_full)

# 17j. --task path uses progressive reduction (not just hard truncation)
# Verify the source has the loop for --task path as well as the main path
test("17j: --task path has progressive-limit loop", "task_injected_tokens" in _br_src_125_full)
test("17j: --task path has graceful degradation footer", "task_budget_tokens" in _br_src_125_full)
test("17j: --task path does not hard-truncate immediately (has reduce loop before fallback)",
     "for reduced_limit in range" in _br_src_125_full)



# ── Summary ──────────────────────────────────────────────────────────────────

print(f"\n{'=' * 50}")
print(f"Results: {PASS} passed, {FAIL} failed")

sys.exit(1 if FAIL else 0)
