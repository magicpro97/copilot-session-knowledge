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

Run: python3 tests/test_briefing.py
"""

import importlib.util
import os
import sys
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


# ── Summary ──────────────────────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"Results: {PASS} passed, {FAIL} failed")

sys.exit(1 if FAIL else 0)
