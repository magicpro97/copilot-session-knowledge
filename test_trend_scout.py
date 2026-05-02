#!/usr/bin/env python3
"""
test_trend_scout.py — Regression tests for trend-scout.py.

Tests (no network calls — all GitHub API interactions are mocked):
  - Config loading: defaults, JSON override, deep merge
  - repo_marker: deterministic, collision-resistant
  - extract_markers_from_body: parsing correctness
  - score_repo: keyword/topic/star/recency scoring
  - shortlist_repos: filtering, dedup, max cap
  - _derive_problem / _derive_strengths / _derive_weaknesses / _derive_learnings
  - render_issue_body: marker included, required sections present
  - GitHubClient: rate-limit awareness, error handling (mocked)
  - get_existing_markers: pagination and state handling (mocked)
  - CLI: --dry-run, --search-only, --repo, --limit, --explain (subprocess)
  - JSON config file roundtrip
  - Multi-lane discovery: adjacent lane, jcode-class replay, cross-lane scoring
  - build_discovery_explain: per-lane stats, shortlist annotations

Run: python3 test_trend_scout.py
"""

import io
import json
import os
import subprocess
import sys
import time
import urllib.error
import unittest.mock as mock
from pathlib import Path
from importlib import import_module

# Fix Windows console encoding (needed because this file prints emoji)
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Allow import of trend-scout (hyphenated module name requires importlib)
REPO = Path(__file__).parent
sys.path.insert(0, str(REPO))

PASS = 0
FAIL = 0
SCOUT = REPO / "trend-scout.py"
CONFIG_FILE = REPO / "trend-scout-config.json"
GOLDSET_FILE = REPO / "trend-scout-goldset.json"
WORKFLOW_FILE = REPO / ".github" / "workflows" / "trend-scout.yml"

# Scratch dir — project-local, no /tmp
SCRATCH = REPO / ".test-scratch" / "trend-scout-tests"
SCRATCH.mkdir(parents=True, exist_ok=True)


def test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


def run_cli(*args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(
        [sys.executable, str(SCOUT), *args],
        capture_output=True,
        text=True,
        env=env,
        encoding="utf-8",
        errors="replace",
    )


# Import module under test
ts = import_module("trend-scout")


# ─── Fixtures ─────────────────────────────────────────────────────────────────

REPO_FIXTURE: dict = {
    "full_name": "someuser/ai-knowledge-base",
    "name": "ai-knowledge-base",
    "description": "Index your AI coding sessions into a searchable sqlite fts5 knowledge base",
    "html_url": "https://github.com/someuser/ai-knowledge-base",
    "created_at": "2023-01-15T10:00:00Z",
    "pushed_at": "2024-11-01T12:00:00Z",
    "stargazers_count": 250,
    "forks_count": 30,
    "watchers_count": 250,
    "open_issues_count": 5,
    "language": "Python",
    "topics": ["ai-tools", "knowledge-base", "sqlite", "fts5", "python"],
    "fork": False,
    "archived": False,
    "license": {"spdx_id": "MIT"},
}

REPO_ARCHIVED: dict = {**REPO_FIXTURE, "full_name": "x/archived", "archived": True, "fork": False}
REPO_FORK: dict = {**REPO_FIXTURE, "full_name": "x/forked", "fork": True, "archived": False}
REPO_INACTIVE: dict = {
    **REPO_FIXTURE,
    "full_name": "x/inactive",
    "pushed_at": "2020-01-01T00:00:00Z",
    "stargazers_count": 3,
    "fork": False,
    "archived": False,
}


# ─── 1. Config ────────────────────────────────────────────────────────────────

print("\n📋 Config Loading")

# Default config has all required keys
cfg = ts.load_config(None)
test("default target_repo set", cfg.get("target_repo") == "magicpro97/copilot-session-knowledge")
test("default issue_label set", cfg.get("issue_label") == "trend-scout")
test("default search.seed_keywords non-empty", len(cfg["search"]["seed_keywords"]) > 0)
test("default shortlist.max_candidates > 0", cfg["shortlist"]["max_candidates"] > 0)
test("default dedup.marker_prefix set", cfg["dedup"]["marker_prefix"])

# JSON override
override_cfg_path = SCRATCH / "override_config.json"
override_cfg_path.write_text(
    json.dumps(
        {
            "target_repo": "myorg/myrepo",
            "shortlist": {"max_candidates": 99},
        }
    ),
    encoding="utf-8",
)
cfg2 = ts.load_config(override_cfg_path)
test("override: target_repo changed", cfg2["target_repo"] == "myorg/myrepo")
test("override: shortlist.max_candidates overridden", cfg2["shortlist"]["max_candidates"] == 99)
test("override: nested search still has defaults", len(cfg2["search"]["seed_keywords"]) > 0)

# Explicit nulls should preserve defaults
null_cfg_path = SCRATCH / "null_override_config.json"
null_cfg_path.write_text(
    json.dumps(
        {
            "analysis": {
                "model": None,
                "endpoint": None,
                "timeout": None,
                "token_env": None,
            }
        }
    ),
    encoding="utf-8",
)
cfg_null = ts.load_config(null_cfg_path)
test("null override keeps default analysis model", cfg_null["analysis"]["model"] == ts.DEFAULT_MODELS_MODEL)
test("null override keeps default analysis endpoint", cfg_null["analysis"]["endpoint"] == ts.MODELS_API_ENDPOINT)
test("null override keeps default analysis timeout", cfg_null["analysis"]["timeout"] == 30)
test("null override keeps default analysis token env", cfg_null["analysis"]["token_env"] == "GITHUB_MODELS_TOKEN")

# Malformed JSON falls back to defaults gracefully
bad_cfg_path = SCRATCH / "bad_config.json"
bad_cfg_path.write_text("{not valid json}", encoding="utf-8")
cfg3 = ts.load_config(bad_cfg_path)
test("malformed config falls back to defaults", cfg3["target_repo"] == "magicpro97/copilot-session-knowledge")

# Config file on disk is valid JSON
test("trend-scout-config.json exists", CONFIG_FILE.exists())
if CONFIG_FILE.exists():
    try:
        disk_cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        test("trend-scout-config.json is valid JSON", True)
        test("disk config has target_repo", "target_repo" in disk_cfg)
        test(
            "disk config analysis endpoint uses models.github.ai",
            disk_cfg.get("analysis", {}).get("endpoint", "").startswith("https://models.github.ai/"),
        )
        test("disk config analysis model is publisher-qualified", "/" in disk_cfg.get("analysis", {}).get("model", ""))
    except Exception as e:
        test("trend-scout-config.json is valid JSON", False, str(e))


# ─── 2. Markers ───────────────────────────────────────────────────────────────

print("\n🔖 Deduplication Markers")

m1 = ts.repo_marker("owner/repo")
m2 = ts.repo_marker("owner/repo")
m3 = ts.repo_marker("Owner/Repo")  # case-insensitive
m4 = ts.repo_marker("other/repo")

test("marker is deterministic", m1 == m2)
test("marker is case-insensitive (full_name)", m1 == m3)
test("different repos produce different markers", m1 != m4)
test("marker contains HTML comment syntax", m1.startswith("<!--") and m1.endswith("-->"))
test("marker contains expected prefix", "trend-scout:repo:" in m1)
test("marker hash is 16 hex chars", bool(__import__("re").search(r"trend-scout:repo:[a-f0-9]{16}", m1)))

# extract_markers_from_body
body_with_markers = (
    "Some text\n<!-- trend-scout:repo:abcdef1234567890 -->\nMore text\n<!-- trend-scout:repo:0000111122223333 -->\n"
)
prefix = "trend-scout:repo:"
found = ts.extract_markers_from_body(body_with_markers, prefix)
test("extract finds 2 markers", len(found) == 2)
test("extract finds correct marker 1", "<!-- trend-scout:repo:abcdef1234567890 -->" in found)
test("extract finds correct marker 2", "<!-- trend-scout:repo:0000111122223333 -->" in found)

empty_found = ts.extract_markers_from_body("no markers here", prefix)
test("extract returns empty set on no markers", len(empty_found) == 0)

# Marker roundtrip: repo_marker then extract
rt_marker = ts.repo_marker("roundtrip/test-repo", prefix)
rt_found = ts.extract_markers_from_body(f"body text\n{rt_marker}\nmore", prefix)
test("marker roundtrip: generated marker is extractable", rt_marker in rt_found)


# ─── 3. Scoring ───────────────────────────────────────────────────────────────

print("\n📊 Scoring / Shortlisting")

default_cfg = ts.load_config(None)

# High-relevance fixture should score higher than unrelated repo
unrelated: dict = {
    "full_name": "someone/unrelated-project",
    "name": "unrelated-project",
    "description": "A web framework for building APIs",
    "topics": ["web", "api", "rest"],
    "stargazers_count": 50,
    "forks_count": 5,
    "pushed_at": "2024-10-01T00:00:00Z",
    "fork": False,
    "archived": False,
    "language": "Go",
}

score_good = ts.score_repo(REPO_FIXTURE, default_cfg)
score_bad = ts.score_repo(unrelated, default_cfg)
test("relevant repo scores higher than unrelated", score_good > score_bad, f"good={score_good}, bad={score_bad}")
test("score is float", isinstance(score_good, float))
test("score is non-negative", score_good >= 0)

# Shortlisting
candidates = [REPO_FIXTURE, unrelated, REPO_FORK, REPO_ARCHIVED, REPO_INACTIVE]
sl_cfg = {
    **default_cfg,
    "shortlist": {**default_cfg["shortlist"], "max_candidates": 3, "min_score": 0.0, "exclude_forks": True},
}
shortlisted = ts.shortlist_repos(candidates, sl_cfg)
test("shortlist caps at max_candidates", len(shortlisted) <= 3)
test("shortlist excludes forks (exclude_forks=True)", all(not r.get("fork") for r in shortlisted))
test("shortlist deduplicates by full_name", len({r["full_name"] for r in shortlisted}) == len(shortlisted))
test(
    "shortlist sorted descending by score",
    (lambda sl: all(ts.score_repo(sl[i], sl_cfg) >= ts.score_repo(sl[i + 1], sl_cfg) for i in range(len(sl) - 1)))(
        shortlisted
    ),
)

# min_score filter
high_min_cfg = {**default_cfg, "shortlist": {**default_cfg["shortlist"], "min_score": 9999.0}}
shortlisted_none = ts.shortlist_repos(candidates, high_min_cfg)
test("min_score=9999 yields empty shortlist", len(shortlisted_none) == 0)

# ─── Required gold-set shortlist-retention (crowd-out regression) ──────────────
print("\n📌 Gold-set Required-Retention")

_REQUIRED_REPO: dict = {
    **REPO_FIXTURE,
    "full_name": "test-owner/required-strategic",
    "name": "required-strategic",
    "description": "strategic adjacent repo that must survive crowd-out",
    "stargazers_count": 10,
    "topics": ["coding-agent"],
    "fork": False,
    "archived": False,
}
# Build 5 high-scoring filler repos that would crowd out _REQUIRED_REPO
_FILLER_REPOS: list[dict] = [
    {
        **REPO_FIXTURE,
        "full_name": f"filler/repo-{i}",
        "name": f"repo-{i}",
        "description": "Index AI coding sessions into fts5 knowledge base with session support",
        "stargazers_count": 5000 + i,
        "topics": ["ai-tools", "knowledge-base", "sqlite", "fts5"],
        "fork": False,
        "archived": False,
    }
    for i in range(5)
]

_retention_goldset: dict = {
    "path": "synthetic",
    "entries": [
        {
            "repo": "test-owner/required-strategic",
            "required": True,
            "min_score": 0.1,
            "expected_lane": "adjacent-ai-dev",
            "category": "adjacent-coding-agent",
            "notes": "test required repo",
        }
    ],
}

# Crowd-out scenario: max_candidates=5 with 5 high-scoring fillers + 1 required repo
_crowd_cfg = {**default_cfg, "shortlist": {**default_cfg["shortlist"], "max_candidates": 5, "min_score": 0.0}}
_crowd_candidates = _FILLER_REPOS + [_REQUIRED_REPO]

# Without goldset pinning: required repo should be displaced by fillers
_no_pin = ts.shortlist_repos(_crowd_candidates, _crowd_cfg, goldset={"path": "synthetic", "entries": []})
_required_in_no_pin = any(r.get("full_name") == "test-owner/required-strategic" for r in _no_pin)

# With goldset pinning: required repo must survive
_with_pin = ts.shortlist_repos(_crowd_candidates, _crowd_cfg, goldset=_retention_goldset)
_required_in_with_pin = any(r.get("full_name") == "test-owner/required-strategic" for r in _with_pin)

test(
    "crowd-out: required repo absent from shortlist when no goldset pinning",
    not _required_in_no_pin,
    f"shortlisted={[r.get('full_name') for r in _no_pin]}",
)
test(
    "crowd-out: required repo retained in shortlist with goldset pinning",
    _required_in_with_pin,
    f"shortlisted={[r.get('full_name') for r in _with_pin]}",
)
test("crowd-out: shortlist still respects max_candidates cap", len(_with_pin) <= 5, f"len={len(_with_pin)}")
test(
    "crowd-out: result is sorted descending by score",
    all(
        ts.score_repo(_with_pin[i], _crowd_cfg, term_set=ts._build_global_term_set(_crowd_cfg))
        >= ts.score_repo(_with_pin[i + 1], _crowd_cfg, term_set=ts._build_global_term_set(_crowd_cfg))
        for i in range(len(_with_pin) - 1)
    ),
    f"shortlisted={[r.get('full_name') for r in _with_pin]}",
)

# Non-required repo with required=False should NOT be pinned
_non_req_goldset: dict = {
    "path": "synthetic",
    "entries": [
        {
            "repo": "test-owner/required-strategic",
            "required": False,
            "min_score": 0.1,
        }
    ],
}
_no_pin_non_req = ts.shortlist_repos(_crowd_candidates, _crowd_cfg, goldset=_non_req_goldset)
test(
    "crowd-out: non-required goldset repo is NOT pinned (still crowded out)",
    not any(r.get("full_name") == "test-owner/required-strategic" for r in _no_pin_non_req),
    f"shortlisted={[r.get('full_name') for r in _no_pin_non_req]}",
)

# Required repos may preempt non-required repos, but the final shortlist still
# respects max_candidates even when required entries themselves exceed the cap.
_many_required_repos: list[dict] = [
    {
        **_REQUIRED_REPO,
        "full_name": f"test-owner/required-{i}",
        "name": f"required-{i}",
        "description": f"strategic adjacent required repo {i}",
        "stargazers_count": 100 + i,
    }
    for i in range(6)
]
_many_required_goldset: dict = {
    "path": "synthetic",
    "entries": [{"repo": repo["full_name"], "required": True, "min_score": 0.1} for repo in _many_required_repos],
}
_many_required_shortlist = ts.shortlist_repos(_many_required_repos, _crowd_cfg, goldset=_many_required_goldset)
_many_required_terms = ts._build_global_term_set(_crowd_cfg)
_many_required_expected = [
    repo["full_name"]
    for repo in sorted(
        _many_required_repos,
        key=lambda repo: ts.score_repo(repo, _crowd_cfg, term_set=_many_required_terms),
        reverse=True,
    )[: _crowd_cfg["shortlist"]["max_candidates"]]
]
test(
    "crowd-out: required pinning still respects max_candidates when required repos exceed cap",
    len(_many_required_shortlist) == _crowd_cfg["shortlist"]["max_candidates"],
    f"len={len(_many_required_shortlist)} shortlisted={[r.get('full_name') for r in _many_required_shortlist]}",
)
test(
    "crowd-out: over-cap required retention keeps the top-scoring required repos",
    [r.get("full_name") for r in _many_required_shortlist] == _many_required_expected,
    f"shortlisted={[r.get('full_name') for r in _many_required_shortlist]} expected={_many_required_expected}",
)


# ─── 4. Heuristic Derivation ──────────────────────────────────────────────────

print("\n🧠 Heuristic Derivation")

# _derive_problem
problem_with_desc = ts._derive_problem(REPO_FIXTURE, "readme text")
test("_derive_problem uses description when available", problem_with_desc == REPO_FIXTURE["description"])

no_desc_repo: dict = {**REPO_FIXTURE, "description": ""}
problem_from_readme = ts._derive_problem(no_desc_repo, "This is a long enough readme line to be useful here.")
test("_derive_problem falls back to readme when no description", "readme" in problem_from_readme.lower())

problem_fallback = ts._derive_problem(no_desc_repo, "")
test("_derive_problem returns fallback when no desc or readme", "(No description" in problem_fallback)

# _derive_strengths
strengths = ts._derive_strengths(REPO_FIXTURE)
test("_derive_strengths returns non-empty list", len(strengths) > 0)
test("_derive_strengths mentions stars", any("⭐" in s or "star" in s.lower() for s in strengths))

low_star_repo: dict = {**REPO_FIXTURE, "stargazers_count": 3, "forks_count": 2, "topics": []}
low_strengths = ts._derive_strengths(low_star_repo)
test("_derive_strengths handles low-star repo without crashing", isinstance(low_strengths, list))

# _derive_weaknesses
risks = ts._derive_weaknesses(REPO_ARCHIVED)
test("_derive_weaknesses flags archived repos", any("archive" in r.lower() for r in risks))

fork_risks = ts._derive_weaknesses(REPO_FORK)
test("_derive_weaknesses flags forks", any("fork" in r.lower() for r in fork_risks))

inactive_risks = ts._derive_weaknesses(REPO_INACTIVE)
test(
    "_derive_weaknesses flags inactive repos",
    any("inactive" in r.lower() or "push" in r.lower() for r in inactive_risks),
)

# _derive_learnings
our_topics = ["ai-tools", "copilot", "fts5", "knowledge-base", "python", "sqlite"]
learnings = ts._derive_learnings(REPO_FIXTURE, our_topics)
test("_derive_learnings returns non-empty list", len(learnings) > 0)

novel_topic_repo: dict = {**REPO_FIXTURE, "topics": ["novel-topic-xyz", "ai-tools"]}
novel_learnings = ts._derive_learnings(novel_topic_repo, our_topics)
# Novel-only repos now fall back to a concrete architectural review bullet instead of
# emitting a generic "Novel topic signals" dump.
test(
    "_derive_learnings: novel-only repo uses concrete fallback (not generic topic list)",
    any("fts5" in l.lower() or "knowledge" in l.lower() or "session" in l.lower() for l in novel_learnings),
    str(novel_learnings),
)

# Regression: no bare "Novel topics to explore: ..." or "Novel topic signals" dumps
test(
    "_derive_learnings no longer emits bare topic-list bullets",
    not any(l.startswith("Novel topics to explore:") for l in novel_learnings),
)
test(
    "_derive_learnings no longer emits 'Novel topic signals' bullets",
    not any("novel topic signals" in l.lower() for l in novel_learnings),
    str(novel_learnings),
)

# AI-IQ-like fixture: hybrid search + graph intelligence + zero config in description
AI_IQ_LIKE_REPO: dict = {
    **REPO_FIXTURE,
    "full_name": "kobie3717/ai-iq",
    "description": (
        "AI-IQ: Persistent context system for AI coding assistants. "
        "Hybrid search (FTS+semantic), graph intelligence, zero config."
    ),
    "topics": [
        "ai",
        "ai-agents",
        "ai-tools",
        "claude-code",
        "cursor",
        "developer-tools",
        "fts5",
        "knowledge-graph",
        "llm",
        "memory",
        "sqlite",
    ],
}
AI_IQ_README = (
    "Give your AI long-term memory in 1 command.\n"
    'memory-tool add learning "Docker needs network_mode: host"\n'
    'memory-tool search "docker networking"\n'
    "memory-tool dream  # Consolidate duplicates, detect conflicts\n"
    "Hybrid search (FTS+semantic) for context recall.\n"
)

ai_iq_learnings = ts._derive_learnings(AI_IQ_LIKE_REPO, our_topics, AI_IQ_README)

test(
    "_derive_learnings emits hybrid-search bullet for hybrid-search repo",
    any("hybrid" in l.lower() or "semantic" in l.lower() for l in ai_iq_learnings),
    str(ai_iq_learnings),
)

test(
    "_derive_learnings emits graph bullet for knowledge-graph repo",
    any("graph" in l.lower() for l in ai_iq_learnings),
    str(ai_iq_learnings),
)

test(
    "_derive_learnings emits consolidation bullet from readme signal",
    any("consolidat" in l.lower() or "dream" in l.lower() for l in ai_iq_learnings),
    str(ai_iq_learnings),
)

test(
    "_derive_learnings emits CLI bullet from readme memory-tool signal",
    any("cli" in l.lower() or "verb" in l.lower() or "memory-tool" in l.lower() for l in ai_iq_learnings),
    str(ai_iq_learnings),
)

test(
    "_derive_learnings emits Claude Code bullet for claude-code topic",
    any("claude" in l.lower() for l in ai_iq_learnings),
    str(ai_iq_learnings),
)

test(
    "_derive_learnings bullets are narrative (contain 'could', 'e.g.', 'would', or 'suggest')",
    all(
        "could" in l.lower() or "e.g." in l.lower() or "would" in l.lower() or "suggest" in l.lower()
        for l in ai_iq_learnings
    ),
    str(ai_iq_learnings),
)

test(
    "_derive_learnings: no bare 'Novel topics to explore:' bullet for ai-iq-like repo",
    not any(l.startswith("Novel topics to explore:") for l in ai_iq_learnings),
)

# Readme-only signals: description is minimal, hints come solely from readme_excerpt
readme_only_repo: dict = {**REPO_FIXTURE, "description": "A tool", "topics": []}
readme_learnings = ts._derive_learnings(
    readme_only_repo,
    our_topics,
    "This project uses hybrid FTS + semantic search to consolidate memories and detect conflicts.",
)
test(
    "_derive_learnings fires on readme_excerpt signals (not just description)",
    any("hybrid" in l.lower() or "semantic" in l.lower() or "consolidat" in l.lower() for l in readme_learnings),
    str(readme_learnings),
)

# Fallback is concrete when no signals fire
no_signal_repo: dict = {**REPO_FIXTURE, "description": "A simple utility", "topics": []}
fallback_learnings = ts._derive_learnings(no_signal_repo, our_topics, "")
test(
    "_derive_learnings fallback is concrete (mentions FTS5 or knowledge-base)",
    any("fts5" in l.lower() or "knowledge" in l.lower() or "session" in l.lower() for l in fallback_learnings),
    str(fallback_learnings),
)

# GitHub Models helpers
sanitized_bullet = ts._sanitize_learning_bullet("  **Pattern**: line 1\n\nline 2 <b>tag</b>  ")
test(
    "_sanitize_learning_bullet collapses embedded newlines",
    sanitized_bullet is not None and "\n" not in sanitized_bullet,
    str(sanitized_bullet),
)
test(
    "_sanitize_learning_bullet strips raw HTML tags",
    sanitized_bullet is not None and "<b>" not in sanitized_bullet and "</b>" not in sanitized_bullet,
    str(sanitized_bullet),
)

null_content_client = mock.Mock()
null_content_client.chat_completions.return_value = {"choices": [{"message": {"content": None}}]}
null_content_learnings = ts._analyze_repo_with_models(
    REPO_FIXTURE,
    "readme text",
    our_topics,
    null_content_client,
    model=ts.DEFAULT_MODELS_MODEL,
    temperature=0.2,
    max_tokens=800,
    max_learnings=5,
)
test("_analyze_repo_with_models returns None on null content", null_content_learnings is None)

# Regression: substring false positives for CLI and sync detectors
cli_fp_repo: dict = {**REPO_FIXTURE, "description": "A HTTP client using click for async operations", "topics": []}
cli_fp_learnings = ts._derive_learnings(cli_fp_repo, our_topics, "client click async processing")
test(
    "_derive_learnings: 'client' does NOT trigger CLI bullet",
    not any("cli verb" in l.lower() or "add/search/update" in l.lower() for l in cli_fp_learnings),
    str(cli_fp_learnings),
)
test(
    "_derive_learnings: 'click' does NOT trigger CLI bullet",
    not any("cli verb" in l.lower() or "add/search/update" in l.lower() for l in cli_fp_learnings),
    str(cli_fp_learnings),
)
test(
    "_derive_learnings: 'async' does NOT trigger cross-env sync bullet",
    not any(
        "windows" in l.lower() or "wsl" in l.lower() or "cross-environment sync" in l.lower() for l in cli_fp_learnings
    ),
    str(cli_fp_learnings),
)

# Positive: exact word "cli" still fires correctly
cli_exact_repo: dict = {**REPO_FIXTURE, "description": "A cli tool with sync support for knowledge", "topics": []}
cli_exact_learnings = ts._derive_learnings(cli_exact_repo, our_topics, "")
test(
    "_derive_learnings: bare 'cli' word still triggers CLI bullet",
    any("cli verb" in l.lower() or "add/search/update" in l.lower() for l in cli_exact_learnings),
    str(cli_exact_learnings),
)
# Bare "sync" alone is NOT sufficient — cross-platform signal is required.
test(
    "_derive_learnings: bare 'sync' alone does NOT trigger cross-env sync bullet",
    not any("cross-environment sync" in l.lower() or "sync-knowledge" in l.lower() for l in cli_exact_learnings),
    str(cli_exact_learnings),
)
# Positive: explicit cross-platform signal does fire the sync bullet
wsl_sync_repo: dict = {**REPO_FIXTURE, "description": "A knowledge tool with wsl cross-platform support", "topics": []}
wsl_sync_learnings = ts._derive_learnings(wsl_sync_repo, our_topics, "")
test(
    "_derive_learnings: 'wsl' keyword triggers cross-env sync bullet",
    any("cross-environment sync" in l.lower() or "sync-knowledge" in l.lower() for l in wsl_sync_learnings),
    str(wsl_sync_learnings),
)

# Cap: output never exceeds MAX_HEURISTIC_LEARNINGS even for high-signal repos
test(
    "_derive_learnings: result capped at MAX_HEURISTIC_LEARNINGS",
    len(ai_iq_learnings) <= ts._MAX_HEURISTIC_LEARNINGS,
    f"got {len(ai_iq_learnings)} bullets",
)

# Editor integration: only fires on topic match, NOT bare keyword in description/readme
editor_kw_repo: dict = {**REPO_FIXTURE, "description": "Move cursor position in vscode extension", "topics": []}
editor_kw_learnings = ts._derive_learnings(editor_kw_repo, our_topics, "check cursor and vscode settings")
test(
    "_derive_learnings: editor keywords alone do NOT trigger editor bullet",
    not any("editor integration" in l.lower() for l in editor_kw_learnings),
    str(editor_kw_learnings),
)

editor_topic_repo: dict = {**REPO_FIXTURE, "description": "A memory tool", "topics": ["cursor", "ai-tools"]}
editor_topic_learnings = ts._derive_learnings(editor_topic_repo, our_topics, "")
test(
    "_derive_learnings: 'cursor' in topics DOES trigger editor bullet",
    any("editor integration" in l.lower() for l in editor_topic_learnings),
    str(editor_topic_learnings),
)

# Claude Code prioritised: fires as first bullet when topic matches
claude_code_repo: dict = {
    **REPO_FIXTURE,
    "description": "Hybrid search semantic knowledge",
    "topics": ["claude-code", "knowledge-graph"],
}
claude_code_learnings = ts._derive_learnings(claude_code_repo, our_topics, "")
test(
    "_derive_learnings: claude-code topic produces a bullet",
    any("claude" in l.lower() for l in claude_code_learnings),
    str(claude_code_learnings),
)
test(
    "_derive_learnings: claude-code bullet is first (highest priority)",
    claude_code_learnings and "claude" in claude_code_learnings[0].lower(),
    str(claude_code_learnings),
)


# ─── Issue #3 Regression Tests ────────────────────────────────────────────────

print("\n🐛 Issue #3 — Heuristic Quality Regressions")

# --- Portability false positive: Python 'from x import y' must NOT fire portability bullet ---
# Confirmed false positive from issue3-output-audit: portability heuristic matched 'import'
# substring in 'from ai_iq import Memory' code block, producing a spurious portability bullet.
# Fix: remove 'import' from portability keyword list; keep only 'export', 'portable', 'backup'.
portability_fp_repo: dict = {**REPO_FIXTURE, "description": "A Python library", "topics": []}
portability_fp_readme = "## Quick Start\n\n```python\nfrom ai_iq import Memory\nm = Memory()\nm.add('learning')\n```\n"
portability_fp_learnings = ts._derive_learnings(portability_fp_repo, our_topics, portability_fp_readme)
test(
    "issue#3 portability FP: 'from x import y' Python import does NOT trigger portability bullet",
    not any("portab" in l.lower() or "export/import" in l.lower() for l in portability_fp_learnings),
    str(portability_fp_learnings),
)

# Positive: genuine portability signals ('export', 'backup', 'portable') still fire
portability_positive_repo: dict = {**REPO_FIXTURE, "description": "Export and backup tool", "topics": []}
portability_positive_learnings = ts._derive_learnings(portability_positive_repo, our_topics, "")
test(
    "issue#3 portability positive: 'export' in description still triggers portability bullet",
    any("portab" in l.lower() or "export" in l.lower() for l in portability_positive_learnings),
    str(portability_positive_learnings),
)

portability_backup_learnings = ts._derive_learnings(
    {**REPO_FIXTURE, "description": "Backup and restore for knowledge", "topics": []},
    our_topics,
    "",
)
test(
    "issue#3 portability positive: 'backup' in description still triggers portability bullet",
    any("portab" in l.lower() or "backup" in l.lower() for l in portability_backup_learnings),
    str(portability_backup_learnings),
)

# --- Offline/no-cloud heuristic: README with offline posture should fire a bullet ---
# Confirmed missing signal from issue3-output-audit (reachable within 1500-char window).
# ai-iq README: "No cloud dependencies — Works offline, owns your data, zero API keys"
# This directly mirrors this repo's local-SQLite-no-server design.
offline_repo: dict = {**REPO_FIXTURE, "description": "A local knowledge tool", "topics": []}
offline_readme_variants = [
    "Works offline, owns your data, zero API keys",
    "No cloud dependencies — offline first",
    "no-cloud no cloud design zero api key",
]
for _offline_hint in offline_readme_variants:
    _offline_learnings = ts._derive_learnings(offline_repo, our_topics, _offline_hint)
    test(
        f"issue#3 offline heuristic: '{_offline_hint[:40]}...' triggers offline/no-cloud bullet",
        any(
            "offline" in l.lower() or "no-cloud" in l.lower() or "no cloud" in l.lower() or "cloud" in l.lower()
            for l in _offline_learnings
        ),
        str(_offline_learnings),
    )

# --- README window / dead-path alignment ---
# issue3-output-audit confirmed: _derive_learnings uses hint[:3000] internally, but
# enrich_stage hard-caps the excerpt at readme_max_chars=1500 before passing it in.
# The [:3000] slice in _derive_learnings is therefore dead code.
# Fix (issue3-config-docs): readme_max_chars should be aligned with the hint window.

# The config readme_max_chars should be ≥ 2000 after the fix, so the 3000-char internal
# hint slice is no longer dead code (i.e., enrich_stage actually passes enough characters).
_cfg_readme_max = ts.load_config(None)["enrichment"]["readme_max_chars"]
test(
    "issue#3 readme window alignment: readme_max_chars config is ≥2000 (dead-path fix, issue3-config-docs)",
    _cfg_readme_max >= 2000,
    f"readme_max_chars={_cfg_readme_max} — expected ≥2000 to activate the 3000-char hint window",
)

# Verify the binding: a signal within readme_max_chars is always detectable.
# Build a readme where the offline signal lands right before the readme_max_chars boundary.
_safe_padding = max(0, _cfg_readme_max - 60)
_boundary_readme = "x" * _safe_padding + " Works offline, owns your data, zero API keys"
_boundary_learnings = ts._derive_learnings(offline_repo, our_topics, _boundary_readme)
test(
    "issue#3 readme window binding: signal within readme_max_chars boundary is detectable",
    any("offline" in l.lower() or "no-cloud" in l.lower() or "cloud" in l.lower() for l in _boundary_learnings),
    f"readme_max_chars={_cfg_readme_max}, signal at ~char {_safe_padding}: {_boundary_learnings}",
)

# --- Reflexion heuristic: Java/general reflection contexts must NOT fire structured reflexion bullet ---
# Opus review found that the bare `\breflect\b` regex in the heuristic matches unrelated
# Java/general reflection contexts — e.g. "java.lang.reflect" (package path) or
# "java reflect api" (bare 'reflect' word in non-agent context).
# Fix: narrow the heuristic so that bare 'reflect' alone is insufficient; require
# co-occurrence with agent/AI context, OR drop the bare-word regex entirely in favour of
# the specific keyword list ('reflexion', 'reflect-load', 'structured reflection', etc.).

_reflect_base_repo: dict = {**REPO_FIXTURE, "description": "", "topics": []}


def _fires_reflexion(learnings: list) -> bool:
    """Return True if any learning bullet describes the structured reflexion workflow."""
    return any("structured refle" in l.lower() or "reflexion workflow" in l.lower() for l in learnings)


# Negative regression — Java package path containing bare 'reflect' word
_java_pkg_learnings = ts._derive_learnings(
    _reflect_base_repo,
    our_topics,
    "Uses java.lang.reflect for runtime class inspection and method invocation",
)
test(
    "issue#3 reflexion FP: 'java.lang.reflect' package path does NOT trigger structured reflexion bullet",
    not _fires_reflexion(_java_pkg_learnings),
    str(_java_pkg_learnings),
)

# Negative regression — bare 'reflect' word in general Java/introspection description
_java_reflect_word_learnings = ts._derive_learnings(
    {**_reflect_base_repo, "description": "Java reflect api for bytecode introspection"},
    our_topics,
    "",
)
test(
    "issue#3 reflexion FP: bare 'reflect' in Java library description does NOT trigger structured reflexion bullet",
    not _fires_reflexion(_java_reflect_word_learnings),
    str(_java_reflect_word_learnings),
)

# Negative regression — general programming 'reflect' context (readme only)
_general_reflect_learnings = ts._derive_learnings(
    _reflect_base_repo,
    our_topics,
    "Provides reflect utilities to inspect Python objects at runtime",
)
test(
    "issue#3 reflexion FP: generic 'reflect' introspection context does NOT trigger structured reflexion bullet",
    not _fires_reflexion(_general_reflect_learnings),
    str(_general_reflect_learnings),
)

# Positive control — genuine AI/agent reflexion signal: 'reflexion' keyword
_genuine_reflexion_learnings = ts._derive_learnings(
    _reflect_base_repo,
    our_topics,
    "Implements an agent reflexion loop: pre-task memory recall and post-task structured debriefing",
)
test(
    "issue#3 reflexion positive: 'reflexion' keyword in AI-agent readme DOES trigger structured reflexion bullet",
    _fires_reflexion(_genuine_reflexion_learnings),
    str(_genuine_reflexion_learnings),
)

# Positive control — genuine signal: 'structured reflexion' phrase
_structured_reflexion_learnings = ts._derive_learnings(
    _reflect_base_repo,
    our_topics,
    "Structured reflexion with worked/failed/next fields for outcome-aware learning",
)
test(
    "issue#3 reflexion positive: 'structured reflexion' phrase DOES trigger structured reflexion bullet",
    _fires_reflexion(_structured_reflexion_learnings),
    str(_structured_reflexion_learnings),
)

# Positive control — genuine signal: 'post-mortem' (no false-positive risk)
_post_mortem_learnings = ts._derive_learnings(
    _reflect_base_repo,
    our_topics,
    "Captures post-mortem analysis after each task to feed future briefings",
)
test(
    "issue#3 reflexion positive: 'post-mortem' keyword DOES trigger structured reflexion bullet",
    _fires_reflexion(_post_mortem_learnings),
    str(_post_mortem_learnings),
)


# ─── Issue #5 / #7 Regression Tests ──────────────────────────────────────────
# These tests guard against regression to the "one generic bullet" state seen in
# GitHub issues #5 (martin-papy/qdrant-loader) and #7 (pvliesdonk/markdown-vault-mcp).

print("\n🐛 Issues #5 / #7 — Heuristic learning quality regressions")

# ── Issue #5: qdrant-loader style fixture ─────────────────────────────────────
# Signals: multi-source ingestion, Confluence/JIRA connectors, MCP server, document conversion
QDRANT_LOADER_REPO: dict = {
    **REPO_FIXTURE,
    "full_name": "martin-papy/qdrant-loader",
    "name": "qdrant-loader",
    "description": (
        "Multi-source document ingestion for Qdrant: Confluence, JIRA, Git, and local files. "
        "MCP server support for AI tool integration. Semantic search via Qdrant vector embeddings."
    ),
    "topics": ["qdrant", "confluence", "python", "mcp", "knowledge-base"],
    "language": "Python",
}
QDRANT_LOADER_README = (
    "qdrant-loader ingests documents from Confluence spaces, JIRA projects, "
    "Git repositories, and local files. Document conversion handles PDF and HTML "
    "formats. Exposes an MCP server for tool-callable access to the knowledge base. "
    "Attachment support for binary files embedded in Confluence pages."
)

_qdrant_learnings = ts._derive_learnings(QDRANT_LOADER_REPO, our_topics, QDRANT_LOADER_README)

test(
    "issue#5 qdrant-loader: produces ≥3 concrete bullets (not collapsed to generic only)",
    len(_qdrant_learnings) >= 3,
    str(_qdrant_learnings),
)
test(
    "issue#5 qdrant-loader: connector/ingestion adapter bullet present",
    any("connector" in l.lower() or "ingestion" in l.lower() or "adapter" in l.lower() for l in _qdrant_learnings),
    str(_qdrant_learnings),
)
test(
    "issue#5 qdrant-loader: MCP tool-server bullet present",
    any("mcp" in l.lower() or "model context protocol" in l.lower() for l in _qdrant_learnings),
    str(_qdrant_learnings),
)
test(
    "issue#5 qdrant-loader: hybrid/semantic bullet still present",
    any("hybrid" in l.lower() or "semantic" in l.lower() for l in _qdrant_learnings),
    str(_qdrant_learnings),
)
test(
    "issue#5 qdrant-loader: document conversion bullet present",
    any(
        "document conversion" in l.lower() or "attachment" in l.lower() or "pipeline" in l.lower()
        for l in _qdrant_learnings
    ),
    str(_qdrant_learnings),
)
test(
    "issue#5 qdrant-loader: cap respected",
    len(_qdrant_learnings) <= ts._MAX_HEURISTIC_LEARNINGS,
    f"got {len(_qdrant_learnings)}",
)

# ── Issue #7: markdown-vault-mcp style fixture ────────────────────────────────
# Signals: frontmatter-aware indexing, incremental reindexing, MCP server, FTS5+semantic
MARKDOWN_VAULT_MCP_REPO: dict = {
    **REPO_FIXTURE,
    "full_name": "pvliesdonk/markdown-vault-mcp",
    "name": "markdown-vault-mcp",
    "description": (
        "MCP server for indexing a markdown vault. Frontmatter-aware indexing, "
        "incremental reindexing, FTS5 and semantic search."
    ),
    "topics": ["mcp", "markdown", "obsidian", "semantic-search", "fts5"],
    "language": "Python",
}
MARKDOWN_VAULT_README = (
    "Index your markdown vault with FTS5 and semantic search. "
    "Frontmatter-aware indexing extracts tags and metadata from YAML frontmatter. "
    "Incremental reindex only processes changed files. "
    "Attachment support for embedded documents."
)

_vault_learnings = ts._derive_learnings(MARKDOWN_VAULT_MCP_REPO, our_topics, MARKDOWN_VAULT_README)

test(
    "issue#7 markdown-vault-mcp: produces ≥3 concrete bullets (not collapsed to generic only)",
    len(_vault_learnings) >= 3,
    str(_vault_learnings),
)
test(
    "issue#7 markdown-vault-mcp: frontmatter bullet present",
    any(
        "frontmatter" in l.lower() or "front matter" in l.lower() or "front-matter" in l.lower()
        for l in _vault_learnings
    ),
    str(_vault_learnings),
)
test(
    "issue#7 markdown-vault-mcp: incremental reindex bullet present",
    any("incremental" in l.lower() or "reindex" in l.lower() or "changed" in l.lower() for l in _vault_learnings),
    str(_vault_learnings),
)
test(
    "issue#7 markdown-vault-mcp: MCP tool-server bullet present",
    any("mcp" in l.lower() or "model context protocol" in l.lower() for l in _vault_learnings),
    str(_vault_learnings),
)
test(
    "issue#7 markdown-vault-mcp: hybrid/semantic bullet still present",
    any("hybrid" in l.lower() or "semantic" in l.lower() for l in _vault_learnings),
    str(_vault_learnings),
)
test(
    "issue#7 markdown-vault-mcp: cap respected",
    len(_vault_learnings) <= ts._MAX_HEURISTIC_LEARNINGS,
    f"got {len(_vault_learnings)}",
)

# ── False-positive guards for new heuristics ──────────────────────────────────

# MCP: bare "mcp" substring NOT in topics/server context should not fire
_mcp_fp_repo: dict = {**REPO_FIXTURE, "description": "A Python toolkit for promcptools", "topics": []}
_mcp_fp_learnings = ts._derive_learnings(_mcp_fp_repo, our_topics, "mcptest config and xmcp module")
test(
    "issue#5/#7 MCP FP: 'mcp' embedded in other words does NOT trigger MCP bullet",
    not any("mcp tool" in l.lower() or "model context protocol" in l.lower() for l in _mcp_fp_learnings),
    str(_mcp_fp_learnings),
)

# Connector: generic "multi" or "source" alone must not fire connector bullet
_connector_fp_repo: dict = {
    **REPO_FIXTURE,
    "description": "A multi-threading source code analyser",
    "topics": [],
}
_connector_fp_learnings = ts._derive_learnings(_connector_fp_repo, our_topics, "source analysis tool")
test(
    "issue#5/#7 connector FP: 'multi' and 'source' in generic context do NOT trigger connector bullet",
    not any("connector" in l.lower() or "adapter" in l.lower() for l in _connector_fp_learnings),
    str(_connector_fp_learnings),
)

# Incremental: bare "incremental" without search/index context must not fire reindex bullet
_incr_fp_repo: dict = {
    **REPO_FIXTURE,
    "description": "Incremental backup utility for files",
    "topics": [],
}
_incr_fp_learnings = ts._derive_learnings(_incr_fp_repo, our_topics, "incremental backup algorithm")
test(
    "issue#5/#7 incremental FP: 'incremental backup' does NOT trigger reindex bullet",
    not any("reindex" in l.lower() or "changed-file" in l.lower() for l in _incr_fp_learnings),
    str(_incr_fp_learnings),
)

# Positive: 'reindex' alone IS sufficient to trigger the incremental reindex bullet
_reindex_positive_learnings = ts._derive_learnings(
    {**REPO_FIXTURE, "description": "Tool with reindex support for large collections", "topics": []},
    our_topics,
    "",
)
test(
    "issue#5/#7 incremental positive: 'reindex' in description triggers incremental reindex bullet",
    any("incremental" in l.lower() or "reindex" in l.lower() for l in _reindex_positive_learnings),
    str(_reindex_positive_learnings),
)

# Positive: 'mcp' topic fires MCP bullet
_mcp_topic_positive_learnings = ts._derive_learnings(
    {**REPO_FIXTURE, "description": "A knowledge search tool", "topics": ["mcp", "knowledge-base"]},
    our_topics,
    "",
)
test(
    "issue#5/#7 MCP positive: 'mcp' in topics triggers MCP bullet",
    any("mcp" in l.lower() or "tool-server" in l.lower() for l in _mcp_topic_positive_learnings),
    str(_mcp_topic_positive_learnings),
)

_dedup_probe = [
    "**MCP tool-server surface**: expose query-session.py and briefing.py as MCP tools",
    "**MCP tool-server surface**: expose query-session.py and briefing.py as MCP tools",
]
_dedup_result = ts._dedupe_learning_bullets(_dedup_probe)
test("learnings dedupe: identical bullets collapse to one entry", len(_dedup_result) == 1, str(_dedup_result))

# issue #12: mostly already-implemented bullets + one novel bullet should be vetoed
_already_done_plus_mcp = [
    "**CLI verb patterns**: add/search/update/delete model could streamline query-session.py and learn.py",
    "**Structured reflexion workflow**: post-task reflection fields could extend learn.py --mistake and briefing.py",
    "**Cross-environment sync patterns**: sync strategy could harden sync-knowledge.py for Windows ↔ WSL merging",
    "**MCP tool-server surface**: expose query-session.py and briefing.py as directly callable MCP tools",
]
_issue12_veto, _issue12_reason = ts._should_veto_candidate(
    REPO_FIXTURE,
    "",
    our_topics,
    {"require_domain_signals": 1, "min_distinct_learnings": 2},
    learnings=_already_done_plus_mcp,
)
test(
    "issue#12 already-done noise: vetoed when only one novel insight remains",
    _issue12_veto and "distinct insights" in _issue12_reason,
    _issue12_reason,
)


print("\n📝 Issue Body Rendering")

marker = ts.repo_marker("someuser/ai-knowledge-base")
body = ts.render_issue_body(REPO_FIXTURE, "Sample README content for testing", marker, our_topics)

test("body contains marker", marker in body)
test("body contains repo full_name", "someuser/ai-knowledge-base" in body)
test("body contains 'What problem it solves' section", "What problem it solves" in body)
test("body contains 'Timeline' section", "Timeline" in body)
test("body contains 'Strengths' section", "Strengths" in body)
test("body contains 'Weaknesses' section", "Weaknesses" in body)
test("body contains 'What this repo can learn' section", "What this repo can learn" in body)
test("body contains HTML link to repo", "https://github.com/someuser/ai-knowledge-base" in body)
test("body contains 'Scouted on' date", "Scouted on" in body)
test("body is a string", isinstance(body, str))
test("body is non-trivial length (>500 chars)", len(body) > 500)

# README excerpt appears in details block
body_with_readme = ts.render_issue_body(REPO_FIXTURE, "x" * 2000, marker, our_topics)
test("long readme is truncated in body", "truncated" in body_with_readme)

# Marker uniqueness across two different repos
marker2 = ts.repo_marker("otheruser/different-repo")
body2 = ts.render_issue_body(
    {
        **REPO_FIXTURE,
        "full_name": "otheruser/different-repo",
        "html_url": "https://github.com/otheruser/different-repo",
    },
    "",
    marker2,
    our_topics,
)
test("different repos produce different markers in body", marker not in body2)


# ─── 6. GitHubClient (mocked) ─────────────────────────────────────────────────

print("\n🌐 GitHubClient (mocked)")


# Build a minimal mock response
def make_mock_response(data: dict | list, status: int = 200, headers: dict | None = None) -> mock.MagicMock:
    resp = mock.MagicMock()
    resp.read.return_value = json.dumps(data).encode()
    resp.status = status
    h = {"X-RateLimit-Remaining": "50", "X-RateLimit-Reset": str(int(time.time()) + 60)}
    if headers:
        h.update(headers)
    resp.headers = h
    resp.__enter__ = lambda s: s
    resp.__exit__ = mock.MagicMock(return_value=False)
    return resp


# GET success
with mock.patch("urllib.request.urlopen") as mock_open:
    mock_open.return_value = make_mock_response({"items": [REPO_FIXTURE]})
    client = ts.GitHubClient(token="ghp_test")
    result = client.get(f"{ts.GITHUB_API}/search/repositories", {"q": "test"})
    test("client.get returns parsed JSON", isinstance(result, dict))
    test("client.get returns items from search", "items" in result)

# search_repos
with mock.patch("urllib.request.urlopen") as mock_open:
    mock_open.return_value = make_mock_response({"items": [REPO_FIXTURE, REPO_INACTIVE]})
    client = ts.GitHubClient(token="ghp_test")
    repos = client.search_repos("ai knowledge base sqlite", min_stars=5, max_results=10)
    test("search_repos returns list", isinstance(repos, list))
    test("search_repos returns repo items", len(repos) == 2)

# search_repos_by_topic includes language filter (issue #11 wrong-language noise regression)
with mock.patch.object(ts.GitHubClient, "get", return_value={"items": [REPO_FIXTURE]}) as _mock_topic_get:
    client = ts.GitHubClient(token="ghp_test")
    repos_by_topic = client.search_repos_by_topic("ai-tools", min_stars=5, max_results=10, language="python")
    _topic_params = _mock_topic_get.call_args.args[1] if _mock_topic_get.call_args else {}
    test("search_repos_by_topic returns list", isinstance(repos_by_topic, list))
    test("search_repos_by_topic query includes topic qualifier", "topic:ai-tools" in _topic_params.get("q", ""))
    test("search_repos_by_topic query includes language qualifier", "language:python" in _topic_params.get("q", ""))

# 404 returns None gracefully
import urllib.error as _ue

with mock.patch("urllib.request.urlopen") as mock_open:
    err = _ue.HTTPError(
        url="https://api.github.com/x", code=404, msg="Not Found", hdrs=mock.MagicMock(get=lambda k, d=None: d), fp=None
    )
    mock_open.side_effect = err
    client = ts.GitHubClient()
    result = client.get("https://api.github.com/repos/x/y/readme")
    test("HTTP 404 returns None gracefully", result is None)

# Rate limit tracking
with mock.patch("urllib.request.urlopen") as mock_open:
    reset_ts = int(time.time()) + 5
    mock_open.return_value = make_mock_response(
        {"items": []},
        headers={"X-RateLimit-Remaining": "1", "X-RateLimit-Reset": str(reset_ts)},
    )
    client = ts.GitHubClient(token="ghp_test")
    client.get(f"{ts.GITHUB_API}/search/repositories")
    test("rate limit remaining tracked", client._remaining == 1)
    test("rate limit reset tracked", client._reset_at == reset_ts)

# POST success
with mock.patch("urllib.request.urlopen") as mock_open:
    mock_open.return_value = make_mock_response(
        {"id": 1, "html_url": "https://github.com/owner/repo/issues/1", "number": 1}
    )
    client = ts.GitHubClient(token="ghp_test")
    result = client.create_issue("owner/repo", "Test Issue", "body text", ["trend-scout"])
    test("create_issue returns dict on success", isinstance(result, dict))
    test("create_issue returns html_url", result.get("html_url", "").startswith("https://"))

# ensure_label: label already exists
with mock.patch("urllib.request.urlopen") as mock_open:
    mock_open.return_value = make_mock_response({"name": "trend-scout", "color": "0075ca"})
    client = ts.GitHubClient(token="ghp_test")
    ok = client.ensure_label("owner/repo", "trend-scout")
    test("ensure_label returns True when label exists", ok is True)

# search_stage forwards language filter to primary-lane topic queries
with (
    mock.patch.object(ts.GitHubClient, "search_repos", return_value=[]),
    mock.patch.object(ts.GitHubClient, "search_repos_by_topic", return_value=[]) as _mock_topic_call,
    mock.patch("time.sleep", return_value=None),
):
    _search_cfg = ts.load_config(None)
    _search_cfg["search"]["seed_keywords"] = []
    _search_cfg["search"]["extra_topics"] = ["knowledge-base"]
    _search_cfg["search"]["language"] = "python"
    ts.search_stage(ts.GitHubClient(token="ghp_test"), _search_cfg)
    # check all calls: the primary lane must have forwarded language="python"
    _all_topic_calls = _mock_topic_call.call_args_list
    _primary_lang_calls = [c for c in _all_topic_calls if c.kwargs.get("language") == "python"]
    test(
        "search_stage primary-lane topic search receives configured language",
        len(_primary_lang_calls) > 0,
        f"no calls with language='python'; calls={[c.kwargs for c in _all_topic_calls]}",
    )


# ─── 7. get_existing_markers (mocked) ─────────────────────────────────────────

print("\n🗂  Existing Markers (mocked)")

marker_a = ts.repo_marker("first/repo")
marker_b = ts.repo_marker("second/repo")
issues_page1 = [
    {"number": 1, "body": f"Some body\n{marker_a}\nmore", "pull_request": None},
    {"number": 2, "body": f"Another issue\n{marker_b}", "pull_request": None},
    {"number": 3, "body": "PR-like", "pull_request": {"url": "..."}},  # should be skipped
]
issues_page2: list = []  # empty = end of pagination


def _mock_list_issues(repo, state="all", per_page=100, page=1, labels=None):
    if page == 1:
        return issues_page1
    return issues_page2


with mock.patch.object(ts.GitHubClient, "list_issues", side_effect=_mock_list_issues):
    client = ts.GitHubClient(token="ghp_test")
    found = ts.get_existing_markers(client, "owner/repo", ts.load_config(None))
    test("get_existing_markers finds marker_a", marker_a in found)
    test("get_existing_markers finds marker_b", marker_b in found)
    test("get_existing_markers skips PR entries", len(found) == 2)


# ─── 8. create_stage dry-run (mocked) ─────────────────────────────────────────

print("\n🧪 create_stage dry-run (mocked)")

with (
    mock.patch.object(ts.GitHubClient, "ensure_label", return_value=True),
    mock.patch.object(ts.GitHubClient, "create_issue", return_value=None),
):
    client = ts.GitHubClient(token="ghp_test")
    # Disable veto for general create_stage tests — these repos don't exercise the veto path.
    cfg_c = {**ts.load_config(None), "veto": {"require_domain_signals": 0}}
    enriched = [(REPO_FIXTURE, "readme text"), (REPO_INACTIVE, "")]
    existing: set[str] = set()
    urls = ts.create_stage(enriched, client, cfg_c, existing, dry_run=True, limit=None)
    test("dry-run returns URLs for both repos", len(urls) == 2)
    test("dry-run URLs contain [dry-run] tag", all("[dry-run]" in u for u in urls))
    test("dry-run adds markers to existing set (prevents duplicates)", len(existing) == 2)

# Limit parameter
with (
    mock.patch.object(ts.GitHubClient, "ensure_label", return_value=True),
    mock.patch.object(ts.GitHubClient, "create_issue", return_value=None),
):
    enriched2 = [(REPO_FIXTURE, ""), (REPO_INACTIVE, ""), (unrelated, "")]
    existing2: set[str] = set()
    urls2 = ts.create_stage(enriched2, client, cfg_c, existing2, dry_run=True, limit=1)
    test("limit=1 creates only 1 issue", len(urls2) == 1)

# Dedup: already-seen marker skips
with (
    mock.patch.object(ts.GitHubClient, "ensure_label", return_value=True),
    mock.patch.object(ts.GitHubClient, "create_issue", return_value=None),
):
    pre_existing = {ts.repo_marker(REPO_FIXTURE["full_name"])}
    urls3 = ts.create_stage([(REPO_FIXTURE, "")], client, cfg_c, pre_existing, dry_run=True)
    test("already-seen marker is skipped", len(urls3) == 0)


# ─── 8b. create_stage update path (mocked) ────────────────────────────────────

print("\n🔄 create_stage update path (mocked)")

_marker_update = ts.repo_marker(REPO_FIXTURE["full_name"])
_old_body = "old body content that differs from newly rendered body"
_issue_map_open: dict = {_marker_update: {"number": 42, "state": "open", "body": _old_body, "title": "old title"}}
_issue_map_closed: dict = {_marker_update: {"number": 99, "state": "closed", "body": _old_body, "title": "old title"}}

# dry-run with issue_map: should show [would-update] for existing issue
with mock.patch.object(ts.GitHubClient, "patch_issue", return_value=None) as mock_patch:
    client_u = ts.GitHubClient(token="ghp_test")
    existing_u: set[str] = {_marker_update}  # marker is in existing_markers too
    urls_update_dry = ts.create_stage(
        [(REPO_FIXTURE, "readme text")],
        client_u,
        cfg_c,
        existing_u,
        dry_run=True,
        issue_map=_issue_map_open,
    )
    test(
        "dry-run update: returns a URL for the existing issue number",
        len(urls_update_dry) == 1 and "42" in urls_update_dry[0],
        str(urls_update_dry),
    )
    test("dry-run update: URL contains [dry-run] tag", urls_update_dry and "[dry-run]" in urls_update_dry[0])
    test("dry-run update: patch_issue NOT called in dry-run mode", mock_patch.call_count == 0)

# live update: patch_issue called, body differs
with mock.patch.object(
    ts.GitHubClient, "patch_issue", return_value={"html_url": "https://github.com/x/y/issues/42"}
) as mock_patch2:
    client_u2 = ts.GitHubClient(token="ghp_test")
    existing_u2: set[str] = {_marker_update}
    urls_live_update = ts.create_stage(
        [(REPO_FIXTURE, "readme text")],
        client_u2,
        cfg_c,
        existing_u2,
        dry_run=False,
        issue_map=_issue_map_open,
    )
    test("live update: patch_issue called once", mock_patch2.call_count == 1, f"call_count={mock_patch2.call_count}")
    test(
        "live update: returns URL from patch response",
        urls_live_update == ["https://github.com/x/y/issues/42"],
        str(urls_live_update),
    )
    # Verify patch_issue was NOT called with a state change
    patch_kwargs = mock_patch2.call_args.kwargs if mock_patch2.call_args else {}
    test("live update: patch_issue has no state param (does not reopen closed issues)", "state" not in patch_kwargs)

# unchanged body: should be skipped even when in issue_map
with mock.patch.object(ts.GitHubClient, "patch_issue", return_value=None) as mock_patch3:
    client_u3 = ts.GitHubClient(token="ghp_test")
    # Use the actual rendered body so the comparison matches
    _real_marker = ts.repo_marker(REPO_FIXTURE["full_name"])
    _real_body = ts.render_issue_body(
        REPO_FIXTURE, "readme text", _real_marker, cfg_c.get("search", {}).get("our_topics", [])
    )
    _issue_map_same = {_real_marker: {"number": 77, "state": "open", "body": _real_body}}
    urls_unchanged = ts.create_stage(
        [(REPO_FIXTURE, "readme text")],
        client_u3,
        cfg_c,
        {_real_marker},
        dry_run=False,
        issue_map=_issue_map_same,
    )
    test("unchanged body: patch_issue NOT called", mock_patch3.call_count == 0, f"call_count={mock_patch3.call_count}")
    test("unchanged body: no URL emitted", len(urls_unchanged) == 0, str(urls_unchanged))

# closed issue: marker acts as suppressor (no update write, even when body differs)
with mock.patch.object(
    ts.GitHubClient, "patch_issue", return_value={"html_url": "https://github.com/x/y/issues/99"}
) as mock_patch4:
    client_u4 = ts.GitHubClient(token="ghp_test")
    _closed_dry_out = io.StringIO()
    with mock.patch("sys.stdout", new=_closed_dry_out):
        urls_closed_dry = ts.create_stage(
            [(REPO_FIXTURE, "readme text")],
            client_u4,
            cfg_c,
            {_marker_update},
            dry_run=True,
            issue_map=_issue_map_closed,
        )
    test("closed issue dry-run: suppressed (no would-update URL)", len(urls_closed_dry) == 0, str(urls_closed_dry))
    test(
        "closed issue dry-run: does not print would-update", "Would update issue #99" not in _closed_dry_out.getvalue()
    )

    urls_closed_update = ts.create_stage(
        [(REPO_FIXTURE, "readme text")],
        client_u4,
        cfg_c,
        {_marker_update},
        dry_run=False,
        issue_map=_issue_map_closed,
    )
    test(
        "closed issue update: patch_issue NOT called (suppressed)",
        mock_patch4.call_count == 0,
        f"call_count={mock_patch4.call_count}",
    )
    test("closed issue update: no URL emitted", len(urls_closed_update) == 0, str(urls_closed_update))

# closed issue: suppression short-circuits before models/heuristic/render paths
with (
    mock.patch.object(ts, "_analyze_repo_with_models", return_value=["**LLM**: keep"]) as mock_closed_analyze,
    mock.patch.object(ts, "_derive_learnings", return_value=["**Heuristic**: keep"]) as mock_closed_derive,
    mock.patch.object(ts, "render_issue_body", return_value="rendered body") as mock_closed_render,
    mock.patch.object(ts.GitHubClient, "patch_issue", return_value=None) as mock_closed_patch,
):
    client_u5 = ts.GitHubClient(token="ghp_test")
    urls_closed_short = ts.create_stage(
        [(REPO_FIXTURE, "readme text")],
        client_u5,
        cfg_c,
        {_marker_update},
        dry_run=False,
        issue_map=_issue_map_closed,
        models_client=mock.Mock(),
        analysis_cfg={"enabled": True},
    )
    test("closed issue short-circuit: no URL emitted", len(urls_closed_short) == 0, str(urls_closed_short))
    test(
        "closed issue short-circuit: models analysis NOT called",
        mock_closed_analyze.call_count == 0,
        f"analyze_calls={mock_closed_analyze.call_count}",
    )
    test(
        "closed issue short-circuit: heuristic derivation NOT called",
        mock_closed_derive.call_count == 0,
        f"derive_calls={mock_closed_derive.call_count}",
    )
    test(
        "closed issue short-circuit: issue body render NOT called",
        mock_closed_render.call_count == 0,
        f"render_calls={mock_closed_render.call_count}",
    )
    test(
        "closed issue short-circuit: patch_issue NOT called",
        mock_closed_patch.call_count == 0,
        f"patch_calls={mock_closed_patch.call_count}",
    )


# ─── 8b-reg1. limit=N does NOT drop update-eligible repos appearing after cap ──

print("\n🧪 create_stage: limit does not drop later updates (regression fix 1)")

_marker_f = ts.repo_marker(REPO_FIXTURE["full_name"])
_marker_i = ts.repo_marker(REPO_INACTIVE["full_name"])

# list order: REPO_FIXTURE (new create) → REPO_INACTIVE (update-eligible)
# limit=1 should create REPO_FIXTURE and still update REPO_INACTIVE
_old_body_i = "old body for inactive repo"
_issue_map_update_after_cap: dict = {
    _marker_i: {"number": 55, "state": "open", "body": _old_body_i, "title": "old inactive"},
}

with (
    mock.patch.object(ts.GitHubClient, "ensure_label", return_value=True),
    mock.patch.object(
        ts.GitHubClient, "create_issue", return_value={"html_url": "https://github.com/x/y/issues/NEW"}
    ) as mock_create,
    mock.patch.object(
        ts.GitHubClient, "patch_issue", return_value={"html_url": "https://github.com/x/y/issues/55"}
    ) as mock_patch_reg,
):
    client_reg = ts.GitHubClient(token="ghp_test")
    cfg_reg = {**ts.load_config(None), "veto": {"require_domain_signals": 0}}
    existing_reg: set[str] = set()
    urls_reg = ts.create_stage(
        [(REPO_FIXTURE, "readme"), (REPO_INACTIVE, "readme inactive")],
        client_reg,
        cfg_reg,
        existing_reg,
        dry_run=False,
        limit=1,
        issue_map=_issue_map_update_after_cap,
    )
    test(
        "limit regression: create_issue called once (new create capped at 1)",
        mock_create.call_count == 1,
        f"create_issue call_count={mock_create.call_count}",
    )
    test(
        "limit regression: patch_issue still called for update-eligible repo after cap",
        mock_patch_reg.call_count == 1,
        f"patch_issue call_count={mock_patch_reg.call_count}",
    )
    test(
        "limit regression: both URLs returned (1 new + 1 update)",
        len(urls_reg) == 2,
        f"urls={urls_reg}",
    )


# ─── 8b-reg2. body-unchanged check ignores volatile date/age text ──────────────

print("\n🧪 create_stage: volatile date/age text ignored in body-unchanged check (regression fix 2)")

_marker_vol = ts.repo_marker(REPO_FIXTURE["full_name"])

# Render a body "today" and then simulate it being stored with a different date/age
_body_today = ts.render_issue_body(
    REPO_FIXTURE,
    "readme text",
    _marker_vol,
    ts.load_config(None).get("search", {}).get("our_topics", []),
)

import re as _re

# Simulate a stored body from a previous day by substituting the date pattern
# and incrementing any "N days ago" counters — this is exactly what cross-day
# rendering produces without any substantive content change.
_body_yesterday = _re.sub(
    r"Scouted on \d{4}-\d{2}-\d{2}",
    "Scouted on 2000-01-01",
    _body_today,
    flags=_re.IGNORECASE,
)
_body_yesterday = _re.sub(
    r"last pushed (\d+) days ago",
    lambda m: f"last pushed {int(m.group(1)) + 1} days ago",
    _body_yesterday,
    flags=_re.IGNORECASE,
)

_issue_map_vol: dict = {_marker_vol: {"number": 88, "state": "open", "body": _body_yesterday, "title": "vol title"}}

with mock.patch.object(ts.GitHubClient, "patch_issue", return_value=None) as mock_patch_vol:
    client_vol = ts.GitHubClient(token="ghp_test")
    urls_vol = ts.create_stage(
        [(REPO_FIXTURE, "readme text")],
        client_vol,
        ts.load_config(None),
        {_marker_vol},
        dry_run=False,
        issue_map=_issue_map_vol,
    )
    test(
        "volatile text fix: patch_issue NOT called when only date/age text differs",
        mock_patch_vol.call_count == 0,
        f"patch_issue call_count={mock_patch_vol.call_count}",
    )
    test(
        "volatile text fix: no URL emitted when body is substantively unchanged",
        len(urls_vol) == 0,
        f"urls={urls_vol}",
    )

# Confirm real content change still triggers a patch (guard against over-stripping)
_body_real_change = _body_yesterday.replace(
    REPO_FIXTURE["description"],
    "COMPLETELY DIFFERENT DESCRIPTION",
)
_issue_map_vol_real: dict = {
    _marker_vol: {"number": 88, "state": "open", "body": _body_real_change, "title": "vol title"}
}
with mock.patch.object(
    ts.GitHubClient, "patch_issue", return_value={"html_url": "https://github.com/x/y/issues/88"}
) as mock_patch_real:
    client_vol2 = ts.GitHubClient(token="ghp_test")
    urls_vol2 = ts.create_stage(
        [(REPO_FIXTURE, "readme text")],
        client_vol2,
        ts.load_config(None),
        {_marker_vol},
        dry_run=False,
        issue_map=_issue_map_vol_real,
    )
    test(
        "volatile text fix: patch_issue IS called when substantive content changes",
        mock_patch_real.call_count == 1,
        f"patch_issue call_count={mock_patch_real.call_count}",
    )


# ─── 8c. get_existing_issue_map (mocked) ──────────────────────────────────────

print("\n🗂  get_existing_issue_map (mocked)")

_map_marker_a = ts.repo_marker("owner/repo-a")
_map_marker_b = ts.repo_marker("owner/repo-b")
_map_issues: list = [
    {"number": 10, "state": "open", "title": "T-A", "body": f"body\n{_map_marker_a}", "pull_request": None},
    {"number": 20, "state": "closed", "title": "T-B", "body": f"body\n{_map_marker_b}", "pull_request": None},
    {
        "number": 30,
        "state": "open",
        "title": "T-PR",
        "body": f"{_map_marker_a}",
        "pull_request": {"url": "x"},
    },  # skip PR
]


def _mock_list_issues_map(repo, state="all", per_page=100, page=1, labels=None):
    if page == 1:
        return _map_issues
    return []


with mock.patch.object(ts.GitHubClient, "list_issues", side_effect=_mock_list_issues_map):
    client_map = ts.GitHubClient(token="ghp_test")
    imap = ts.get_existing_issue_map(client_map, "owner/repo", ts.load_config(None))
    test("get_existing_issue_map: marker_a present", _map_marker_a in imap, f"keys={list(imap.keys())}")
    test("get_existing_issue_map: marker_b present", _map_marker_b in imap)
    test("get_existing_issue_map: PR entry skipped (only 2 entries)", len(imap) == 2, f"len={len(imap)}")
    test(
        "get_existing_issue_map: issue number preserved",
        imap[_map_marker_a]["number"] == 10,
        str(imap.get(_map_marker_a)),
    )
    test("get_existing_issue_map: closed state preserved", imap[_map_marker_b]["state"] == "closed")
    # get_existing_markers delegates to issue_map
    with mock.patch.object(ts.GitHubClient, "list_issues", side_effect=_mock_list_issues_map):
        markers_set = ts.get_existing_markers(client_map, "owner/repo", ts.load_config(None))
    test(
        "get_existing_markers still works after refactor", _map_marker_a in markers_set and _map_marker_b in markers_set
    )


# ─── 8d. GitHubClient.patch_issue (mocked) ────────────────────────────────────

print("\n🔧 GitHubClient.patch_issue (mocked)")

_patch_resp = {"number": 5, "html_url": "https://github.com/o/r/issues/5", "state": "closed"}


def _mock_patch_urlopen(req, timeout=None):
    assert req.method == "PATCH", f"expected PATCH, got {req.method}"
    assert "/issues/5" in req.full_url, f"unexpected URL: {req.full_url}"
    body_sent = json.loads(req.data.decode())
    assert "title" in body_sent and "body" in body_sent
    resp = mock.MagicMock()
    resp.read.return_value = json.dumps(_patch_resp).encode()
    resp.headers = mock.MagicMock()
    resp.headers.__iter__ = mock.Mock(return_value=iter([]))
    resp.__enter__ = lambda s: s
    resp.__exit__ = mock.Mock(return_value=False)
    return resp


with mock.patch("urllib.request.urlopen", side_effect=_mock_patch_urlopen):
    client_patch = ts.GitHubClient(token="ghp_test")
    patch_result = client_patch.patch_issue("owner/repo", 5, "New Title", "New body")
    test(
        "patch_issue: returns parsed dict on success",
        isinstance(patch_result, dict) and patch_result.get("number") == 5,
        str(patch_result),
    )
    test("patch_issue: html_url in response", patch_result is not None and "html_url" in patch_result)


# patch_issue returns None on HTTP error
def _mock_patch_404(*args, **kwargs):
    raise urllib.error.HTTPError(
        "https://api.github.com/repos/x/y/issues/999", 404, "Not Found", mock.MagicMock(), None
    )


with mock.patch("urllib.request.urlopen", side_effect=_mock_patch_404):
    client_patch2 = ts.GitHubClient(token="ghp_test")
    patch_fail = client_patch2.patch_issue("x/y", 999, "Title", "Body")
    test("patch_issue: returns None on HTTP 404", patch_fail is None)


print("\n💻 CLI")

test("trend-scout.py exists", SCOUT.exists())

# --help exits 0
r = run_cli("--help")
test("--help exits 0", r.returncode == 0, r.stderr)
test("--help mentions --dry-run", "--dry-run" in r.stdout)
test("--help mentions --search-only", "--search-only" in r.stdout)
test("--help mentions --repo", "--repo" in r.stdout)
test("--help mentions --limit", "--limit" in r.stdout)

# Syntax check
import ast as _ast

try:
    _ast.parse(SCOUT.read_text(encoding="utf-8"))
    test("trend-scout.py passes AST parse", True)
except SyntaxError as e:
    test("trend-scout.py passes AST parse", False, str(e))
except UnicodeDecodeError as e:
    test("trend-scout.py passes AST parse", False, str(e))

# trend-scout-config.json is valid JSON
try:
    json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    test("trend-scout-config.json valid JSON", True)
except Exception as e:
    test("trend-scout-config.json valid JSON", False, str(e))

# --repo validation: bad format exits non-zero
# We cannot easily test this without network, but we can at least check that
# the CLI imports and runs without import errors via a quick --help check
r2 = run_cli("--help")
test("CLI --help is idempotent", r2.returncode == 0)


# ─── 10. _deep_merge ──────────────────────────────────────────────────────────

print("\n🔀 Deep Merge")

base = {"a": 1, "b": {"x": 10, "y": 20}, "c": [1, 2]}
override = {"b": {"x": 99}, "c": [3], "d": "new"}
ts._deep_merge(base, override)
test("deep merge updates nested key", base["b"]["x"] == 99)
test("deep merge preserves untouched nested key", base["b"]["y"] == 20)
test("deep merge overrides list", base["c"] == [3])
test("deep merge adds new key", base["d"] == "new")
test("deep merge preserves top-level key not in override", base["a"] == 1)

# _comment keys are skipped
base2 = {"_comment": "should stay", "target_repo": "a/b"}
ts._deep_merge(base2, {"_comment": "override"})
test("_deep_merge skips _comment keys", base2["_comment"] == "should stay")


# ─── 11. Opus Findings Regression Tests ──────────────────────────────────────

print("\n🛡  Opus Findings Regression")

# --- Finding 1: Self-scouting exclusion ---
self_repo = "magicpro97/copilot-session-knowledge"
self_repo_dict: dict = {
    **REPO_FIXTURE,
    "full_name": self_repo,
    "fork": False,
    "archived": False,
}
self_scout_cfg = {
    **default_cfg,
    "target_repo": self_repo,
    "shortlist": {**default_cfg["shortlist"], "min_score": 0.0, "exclude_forks": False},
}
candidates_with_self = [self_repo_dict, REPO_FIXTURE]
shortlisted_self = ts.shortlist_repos(candidates_with_self, self_scout_cfg)
test(
    "self-scouting: target repo excluded from shortlist",
    all(r["full_name"].lower() != self_repo.lower() for r in shortlisted_self),
    f"shortlist still contains {self_repo}",
)

# Case-insensitive exclusion
self_repo_upper: dict = {**self_repo_dict, "full_name": self_repo.upper()}
shortlisted_upper = ts.shortlist_repos([self_repo_upper, REPO_FIXTURE], self_scout_cfg)
test(
    "self-scouting: target repo excluded case-insensitively",
    all(r["full_name"].lower() != self_repo.lower() for r in shortlisted_upper),
)

# Other repos still shortlisted
test(
    "self-scouting: other repos still shortlisted normally",
    any(r["full_name"] == REPO_FIXTURE["full_name"] for r in shortlisted_self),
)

# --- Finding 2: 403 retry recursion bound ---
import urllib.error as _ue2

# Persistent 403+Retry-After must NOT recurse past one retry
call_count = {"n": 0}


def _always_403(*args, **kwargs):
    call_count["n"] += 1
    err = _ue2.HTTPError(
        url="https://api.github.com/x",
        code=403,
        msg="Forbidden",
        hdrs=mock.MagicMock(get=lambda k, d=None: "1" if k == "Retry-After" else d),
        fp=None,
    )
    raise err


with (
    mock.patch("urllib.request.urlopen", side_effect=_always_403),
    mock.patch("time.sleep"),
):  # don't actually sleep in tests
    client_403 = ts.GitHubClient(token="ghp_test")
    result_403 = client_403.get("https://api.github.com/repos/x/y")
    test(
        "403 retry recursion bound: returns None after bounded retries",
        result_403 is None,
    )
    test(
        "403 retry recursion bound: urlopen called at most 2 times (1 original + 1 retry)",
        call_count["n"] <= 2,
        f"urlopen called {call_count['n']} times",
    )

# --- Finding 3: Dedupe scan drift — label filter propagated ---
label_calls: list[str | None] = []


def _mock_list_issues_capture(repo, state="all", per_page=100, page=1, labels=None):
    label_calls.append(labels)
    return []


with mock.patch.object(ts.GitHubClient, "list_issues", side_effect=_mock_list_issues_capture):
    client_dedup = ts.GitHubClient(token="ghp_test")
    ts.get_existing_markers(client_dedup, "owner/repo", ts.load_config(None))
    test(
        "dedupe scan: list_issues called with trend-scout label",
        all(lbl == "trend-scout" for lbl in label_calls) and len(label_calls) > 0,
        f"labels seen: {label_calls}",
    )

# Custom label in config is also forwarded
label_calls2: list[str | None] = []


def _mock_list_issues_capture2(repo, state="all", per_page=100, page=1, labels=None):
    label_calls2.append(labels)
    return []


custom_label_cfg = {**ts.load_config(None), "issue_label": "my-custom-label"}
with mock.patch.object(ts.GitHubClient, "list_issues", side_effect=_mock_list_issues_capture2):
    client_dedup2 = ts.GitHubClient(token="ghp_test")
    ts.get_existing_markers(client_dedup2, "owner/repo", custom_label_cfg)
    test(
        "dedupe scan: custom label forwarded to list_issues",
        all(lbl == "my-custom-label" for lbl in label_calls2),
        f"labels seen: {label_calls2}",
    )

# --- Finding 5: Models token selection must be explicit ---
with (
    mock.patch.object(ts, "search_stage", return_value=[]),
    mock.patch.object(ts, "shortlist_repos", return_value=[]),
    mock.patch.object(ts, "ModelsClient") as mock_models,
    mock.patch.object(ts, "load_run_state", return_value={}),
    mock.patch.dict(os.environ, {"GITHUB_TOKEN": "ghs_actions_token"}, clear=True),
):
    explicit_token_cfg = ts.load_config(None)
    explicit_token_cfg["analysis"]["enabled"] = True
    explicit_token_cfg["analysis"]["token_env"] = "GITHUB_MODELS_TOKEN"
    ts.run(explicit_token_cfg, dry_run=True, search_only=True)
    test(
        "models auth: missing configured token_env does not silently fall back to GITHUB_TOKEN",
        mock_models.call_count == 0,
        f"ModelsClient call count={mock_models.call_count}",
    )

with (
    mock.patch.object(ts, "search_stage", return_value=[]),
    mock.patch.object(ts, "shortlist_repos", return_value=[]),
    mock.patch.object(ts, "ModelsClient") as mock_models,
    mock.patch.object(ts, "load_run_state", return_value={}),
    mock.patch.dict(os.environ, {"GITHUB_TOKEN": "ghs_actions_token"}, clear=True),
):
    explicit_workflow_cfg = ts.load_config(None)
    explicit_workflow_cfg["analysis"]["enabled"] = True
    explicit_workflow_cfg["analysis"]["token_env"] = "GITHUB_TOKEN"
    ts.run(explicit_workflow_cfg, dry_run=True, search_only=True)
    test(
        "models auth: explicit GITHUB_TOKEN token_env is accepted for Actions workflows",
        mock_models.call_count == 1,
        f"ModelsClient call count={mock_models.call_count}",
    )
    timeout_kw = mock_models.call_args.kwargs.get("timeout") if mock_models.call_args else None
    test(
        "models auth: explicit GITHUB_TOKEN path uses default timeout",
        timeout_kw == 30,
        f"timeout={timeout_kw}",
    )

with (
    mock.patch.object(ts, "search_stage", return_value=[]),
    mock.patch.object(ts, "shortlist_repos", return_value=[]),
    mock.patch.object(ts, "ModelsClient") as mock_models,
    mock.patch.object(ts, "load_run_state", return_value={}),
    mock.patch.dict(os.environ, {"GITHUB_MODELS_TOKEN": "ghm_pat"}, clear=True),
):
    invalid_model_cfg = ts.load_config(None)
    invalid_model_cfg["analysis"]["enabled"] = True
    invalid_model_cfg["analysis"]["model"] = "gpt-4o-mini"
    ts.run(invalid_model_cfg, dry_run=True, search_only=True)
    test(
        "models config: invalid unqualified model id skips ModelsClient construction",
        mock_models.call_count == 0,
        f"ModelsClient call count={mock_models.call_count}",
    )

with (
    mock.patch.object(ts, "search_stage", return_value=[]),
    mock.patch.object(ts, "shortlist_repos", return_value=[]),
    mock.patch.object(ts, "ModelsClient") as mock_models,
    mock.patch.object(ts, "load_run_state", return_value={}),
    mock.patch.dict(os.environ, {"GITHUB_MODELS_TOKEN": "ghm_pat"}, clear=True),
):
    null_timeout_cfg = ts.load_config(None)
    null_timeout_cfg["analysis"]["enabled"] = True
    null_timeout_cfg["analysis"]["timeout"] = None
    ts.run(null_timeout_cfg, dry_run=True, search_only=True)
    timeout_kw = mock_models.call_args.kwargs.get("timeout") if mock_models.call_args else None
    test(
        "models config: null timeout falls back to default without crashing",
        mock_models.call_count == 1 and timeout_kw == 30,
        f"ModelsClient call count={mock_models.call_count}, timeout={timeout_kw}",
    )

analysis_cfg_nulls = {
    "model": None,
    "temperature": None,
    "max_tokens": None,
    "max_learnings": None,
}
with mock.patch.object(ts, "_analyze_repo_with_models", return_value=None) as mock_analyze:
    client = ts.GitHubClient(token="ghp_test")
    urls = ts.create_stage(
        [(REPO_FIXTURE, "readme text")],
        client,
        {**ts.load_config(None), "veto": {"require_domain_signals": 0}},
        set(),
        dry_run=True,
        models_client=mock.Mock(),
        analysis_cfg=analysis_cfg_nulls,
    )
    analyze_kwargs = mock_analyze.call_args.kwargs if mock_analyze.call_args else {}
    test(
        "create_stage: null analysis values use defaults",
        len(urls) == 1
        and analyze_kwargs.get("temperature") == 0.2
        and analyze_kwargs.get("max_tokens") == 800
        and analyze_kwargs.get("max_learnings") == 5
        and analyze_kwargs.get("model") == ts.DEFAULT_MODELS_MODEL,
        str(analyze_kwargs),
    )

# --- Finding 4: Marker spoofing from README excerpt ---
real_marker = ts.repo_marker("real/repo")
fake_marker = ts.repo_marker("fake/spoof-repo")

# Body that embeds a fake marker inside a code fence (README excerpt)
spoofed_body = (
    f"## Issue header\n\n"
    f"<details><summary>README excerpt</summary>\n\n"
    f"```\n"
    f"This README contains a fake marker:\n"
    f"{fake_marker}\n"
    f"```\n</details>\n\n"
    f"---\n"
    f"{real_marker}\n"
)

prefix = "trend-scout:repo:"
extracted = ts.extract_markers_from_body(spoofed_body, prefix)
test(
    "marker spoofing: fake marker inside code fence is NOT extracted",
    fake_marker not in extracted,
    f"fake marker was extracted: {fake_marker}",
)
test(
    "marker spoofing: real marker outside code fence IS extracted",
    real_marker in extracted,
    f"real marker not found; extracted={extracted}",
)
test(
    "marker spoofing: exactly one marker extracted",
    len(extracted) == 1,
    f"extracted {len(extracted)} markers: {extracted}",
)

# Nested/multiple code fences
multi_fence_body = f"```\n{fake_marker}\n```\nsome text\n```python\n# code\n{fake_marker}\n```\n{real_marker}\n"
multi_extracted = ts.extract_markers_from_body(multi_fence_body, prefix)
test(
    "marker spoofing: markers in multiple code fences all ignored",
    fake_marker not in multi_extracted and real_marker in multi_extracted,
    f"extracted={multi_extracted}",
)


# ─── Rowboat Veto Gate ────────────────────────────────────────────────────────

print("\n🔒 Rowboat Veto Gate")

_veto_our_topics = ["ai-tools", "copilot", "fts5", "knowledge-base", "python", "sqlite"]

# _is_only_fallback_learnings
_no_signal_repo = {**REPO_FIXTURE, "description": "A simple utility", "topics": []}
_fallback_l = ts._derive_learnings(_no_signal_repo, _veto_our_topics, "")
test(
    "_is_only_fallback_learnings: True for fallback-only output",
    ts._is_only_fallback_learnings(_fallback_l),
    str(_fallback_l),
)

_signal_l = ts._derive_learnings(AI_IQ_LIKE_REPO, _veto_our_topics, AI_IQ_README)
test(
    "_is_only_fallback_learnings: False for signal-rich output",
    not ts._is_only_fallback_learnings(_signal_l),
    str(_signal_l),
)

test("_is_only_fallback_learnings: False for empty list", not ts._is_only_fallback_learnings([]))

test(
    "_is_only_fallback_learnings: False for multi-bullet list",
    not ts._is_only_fallback_learnings(["bullet 1", "bullet 2"]),
)

# _should_veto_candidate: pre-computed learnings path (wires production to helper)
# _should_veto_candidate
_veto_enabled_cfg = {"require_domain_signals": 1}
_veto_disabled_cfg = {"require_domain_signals": 0}

_veto_ok, _veto_reason = ts._should_veto_candidate(_no_signal_repo, "", _veto_our_topics, _veto_enabled_cfg)
test("_should_veto_candidate: vetoes repo with no domain signals (veto enabled)", _veto_ok, _veto_reason)

_veto_pass, _ = ts._should_veto_candidate(AI_IQ_LIKE_REPO, AI_IQ_README, _veto_our_topics, _veto_enabled_cfg)
test("_should_veto_candidate: passes repo with domain signals", not _veto_pass)

_veto_off, _ = ts._should_veto_candidate(_no_signal_repo, "", _veto_our_topics, _veto_disabled_cfg)
test("_should_veto_candidate: disabled config never vetoes", not _veto_off)

# create_stage with veto enabled: generic repo is skipped
_veto_cfg_c = {**ts.load_config(None), "veto": {"require_domain_signals": 1}}
_no_signal_fixture = {
    **REPO_FIXTURE,
    "full_name": "veto/test-no-signal",
    "description": "A simple utility",
    "topics": [],
}

with (
    mock.patch.object(ts.GitHubClient, "ensure_label", return_value=True),
    mock.patch.object(ts.GitHubClient, "create_issue", return_value=None),
):
    _veto_client = ts.GitHubClient(token="ghp_test")
    _veto_urls = ts.create_stage(
        [(_no_signal_fixture, "")],
        _veto_client,
        _veto_cfg_c,
        set(),
        dry_run=True,
    )
    test("create_stage: vetoed repo (no domain signals) produces no issue URL", len(_veto_urls) == 0, str(_veto_urls))

# Signal-rich repo is NOT vetoed
with (
    mock.patch.object(ts.GitHubClient, "ensure_label", return_value=True),
    mock.patch.object(ts.GitHubClient, "create_issue", return_value=None),
):
    _signal_urls = ts.create_stage(
        [(AI_IQ_LIKE_REPO, AI_IQ_README)],
        _veto_client,
        _veto_cfg_c,
        set(),
        dry_run=True,
    )
    test("create_stage: signal-rich repo is NOT vetoed", len(_signal_urls) == 1, str(_signal_urls))

# Veto does NOT apply to update-eligible repos (is_update path is exempt)
with mock.patch.object(
    ts.GitHubClient, "patch_issue", return_value={"html_url": "https://github.com/x/y/issues/42"}
) as _mock_veto_patch:
    _veto_marker = ts.repo_marker(_no_signal_fixture["full_name"])
    _old_veto_body = "old body content that is different"
    _veto_issue_map = {_veto_marker: {"number": 42, "state": "open", "body": _old_veto_body}}
    _veto_update_urls = ts.create_stage(
        [(_no_signal_fixture, "")],
        _veto_client,
        _veto_cfg_c,
        {_veto_marker},
        dry_run=False,
        issue_map=_veto_issue_map,
    )
    test(
        "create_stage: veto does NOT block update of existing issue",
        _mock_veto_patch.call_count == 1,
        f"patch_issue calls={_mock_veto_patch.call_count}",
    )

# Distinct-insight gate: require multiple independent insights for issue quality
_veto_distinct_cfg = {"require_domain_signals": 1, "min_distinct_learnings": 2}
_single_signal = ["**MCP tool-server surface**: expose query-session.py and briefing.py via MCP for direct agent calls"]
_distinct_veto, _distinct_reason = ts._should_veto_candidate(
    _no_signal_repo, "", _veto_our_topics, _veto_distinct_cfg, learnings=_single_signal
)
test(
    "_should_veto_candidate: min_distinct_learnings vetoes single-insight candidate",
    _distinct_veto and "distinct insights" in _distinct_reason,
    _distinct_reason,
)

_two_signal = [
    "**MCP tool-server surface**: expose query-session.py and briefing.py via MCP for direct agent calls",
    "**Graph-based knowledge linking**: relation graph could connect mistakes and patterns by topic proximity",
]
_distinct_pass, _ = ts._should_veto_candidate(
    _no_signal_repo, "", _veto_our_topics, _veto_distinct_cfg, learnings=_two_signal
)
test("_should_veto_candidate: min_distinct_learnings passes two distinct insights", not _distinct_pass)

# _should_veto_candidate: pre-computed learnings (production-wiring tests)
_precomp_veto, _precomp_reason = ts._should_veto_candidate(
    _no_signal_repo,
    "",
    _veto_our_topics,
    _veto_enabled_cfg,
    learnings=_fallback_l,
)
test("_should_veto_candidate: vetoes with pre-computed fallback learnings", _precomp_veto, _precomp_reason)

_precomp_pass, _ = ts._should_veto_candidate(
    AI_IQ_LIKE_REPO,
    AI_IQ_README,
    _veto_our_topics,
    _veto_enabled_cfg,
    learnings=_signal_l,
)
test("_should_veto_candidate: passes with pre-computed signal-rich learnings", not _precomp_pass)

# LLM-style learnings (non-fallback bullets) also pass the gate
_llm_like_learnings = ["**Pattern**: FTS5 incremental indexing could improve query-session.py recall"]
_llm_veto, _ = ts._should_veto_candidate(
    _no_signal_repo,
    "",
    _veto_our_topics,
    _veto_enabled_cfg,
    learnings=_llm_like_learnings,
)
test("_should_veto_candidate: non-fallback pre-computed (LLM-style) bullets pass the gate", not _llm_veto)

# create_stage calls _should_veto_candidate (not dead code): spy to confirm
with (
    mock.patch.object(ts, "_should_veto_candidate", wraps=ts._should_veto_candidate) as _spy_veto,
    mock.patch.object(ts.GitHubClient, "ensure_label", return_value=True),
    mock.patch.object(ts.GitHubClient, "create_issue", return_value=None),
):
    ts.create_stage(
        [(_no_signal_fixture, "")],
        ts.GitHubClient(token="ghp_test"),
        _veto_cfg_c,
        set(),
        dry_run=True,
    )
    test(
        "create_stage: calls _should_veto_candidate in production path (not dead code)",
        _spy_veto.call_count == 1,
        f"_should_veto_candidate call_count={_spy_veto.call_count}",
    )

# Verify spy passes learnings kwarg (production path sends pre-computed learnings)
with (
    mock.patch.object(ts, "_should_veto_candidate", wraps=ts._should_veto_candidate) as _spy_veto2,
    mock.patch.object(ts.GitHubClient, "ensure_label", return_value=True),
    mock.patch.object(ts.GitHubClient, "create_issue", return_value=None),
):
    ts.create_stage(
        [(AI_IQ_LIKE_REPO, AI_IQ_README)],
        ts.GitHubClient(token="ghp_test"),
        _veto_cfg_c,
        set(),
        dry_run=True,
    )
    _spy_call_kwargs = _spy_veto2.call_args.kwargs if _spy_veto2.call_args else {}
    test(
        "create_stage: passes pre-computed learnings to _should_veto_candidate",
        "learnings" in _spy_call_kwargs and _spy_call_kwargs["learnings"] is not None,
        f"kwargs={_spy_call_kwargs}",
    )


# ─── Grace Window / Run State ─────────────────────────────────────────────────

print("\n⏰ Grace Window / Run State")

import datetime as _dt_gw

_gw_state_file = SCRATCH / "run-state.json"
_gw_state_file.unlink(missing_ok=True)

# load_run_state: returns {} for non-existent file
test("load_run_state: returns {} for non-existent file", ts.load_run_state(_gw_state_file) == {})

# save + load roundtrip
_test_gw_state = {"last_run_utc": "2024-01-01T12:00:00+00:00"}
ts.save_run_state(_test_gw_state, _gw_state_file)
_gw_loaded = ts.load_run_state(_gw_state_file)
test(
    "save_run_state / load_run_state roundtrip",
    _gw_loaded.get("last_run_utc") == "2024-01-01T12:00:00+00:00",
    str(_gw_loaded),
)

# _check_grace_window: disabled (grace_hours=0)
_gw_skip, _gw_reason = ts._check_grace_window(0, {"last_run_utc": "2024-01-01T12:00:00+00:00"})
test("_check_grace_window: disabled (grace_hours=0) never skips", not _gw_skip)

# _check_grace_window: empty state → no skip
_gw_skip2, _ = ts._check_grace_window(20, {})
test("_check_grace_window: empty state → no skip", not _gw_skip2)

# _check_grace_window: recent run → skip
_recent_ts = (_dt_gw.datetime.now(_dt_gw.timezone.utc) - _dt_gw.timedelta(hours=5)).isoformat()
_gw_skip3, _gw_reason3 = ts._check_grace_window(20, {"last_run_utc": _recent_ts})
test("_check_grace_window: last run 5h ago within 20h window → skip", _gw_skip3, _gw_reason3)
test(
    "_check_grace_window: reason mentions elapsed and window",
    "ago" in _gw_reason3 and "grace window" in _gw_reason3.lower(),
    _gw_reason3,
)

# _check_grace_window: old run → no skip
_old_ts = (_dt_gw.datetime.now(_dt_gw.timezone.utc) - _dt_gw.timedelta(hours=25)).isoformat()
_gw_skip4, _ = ts._check_grace_window(20, {"last_run_utc": _old_ts})
test("_check_grace_window: last run 25h ago past 20h window → no skip", not _gw_skip4)

# Grace window integration: run() respects grace window (search_stage not called)
with (
    mock.patch.object(ts, "search_stage", return_value=[]) as _mock_gw_search,
    mock.patch.object(ts, "shortlist_repos", return_value=[]),
    mock.patch.object(ts, "load_run_state", return_value={"last_run_utc": _recent_ts}),
):
    _gw_run_cfg = {
        **ts.load_config(None),
        "run_control": {"grace_window_hours": 20, "state_file": str(_gw_state_file)},
    }
    _gw_exit = ts.run(_gw_run_cfg, dry_run=True, force=False)
    test(
        "run(): grace window active → search_stage NOT called",
        _mock_gw_search.call_count == 0,
        f"search_stage calls={_mock_gw_search.call_count}",
    )
    test("run(): grace window active → exit 0 (not an error)", _gw_exit == 0)

# Grace window bypassed with force=True
with (
    mock.patch.object(ts, "search_stage", return_value=[]) as _mock_gw_force,
    mock.patch.object(ts, "shortlist_repos", return_value=[]),
    mock.patch.object(ts, "load_run_state", return_value={"last_run_utc": _recent_ts}),
):
    _gw_force_cfg = {
        **ts.load_config(None),
        "run_control": {"grace_window_hours": 20, "state_file": str(_gw_state_file)},
    }
    ts.run(_gw_force_cfg, dry_run=True, force=True)
    test(
        "run(): force=True bypasses grace window (search_stage called)",
        _mock_gw_force.call_count >= 1,
        f"search_stage calls={_mock_gw_force.call_count}",
    )

# run() with dry_run=True does NOT persist run state
with (
    mock.patch.object(ts, "search_stage", return_value=[]),
    mock.patch.object(ts, "shortlist_repos", return_value=[]),
    mock.patch.object(ts, "load_run_state", return_value={}),
    mock.patch.object(ts, "save_run_state") as _mock_save_gw,
):
    _dr_cfg = {
        **ts.load_config(None),
        "run_control": {"grace_window_hours": 20, "state_file": str(_gw_state_file)},
    }
    ts.run(_dr_cfg, dry_run=True, force=True)
    test(
        "run(): dry-run does NOT persist run state",
        _mock_save_gw.call_count == 0,
        f"save_run_state calls={_mock_save_gw.call_count}",
    )

# run() with search_only=True does NOT persist run state
with (
    mock.patch.object(ts, "search_stage", return_value=[]),
    mock.patch.object(ts, "shortlist_repos", return_value=[]),
    mock.patch.object(ts, "load_run_state", return_value={}),
    mock.patch.object(ts, "save_run_state") as _mock_save_so,
):
    _so_cfg = {
        **ts.load_config(None),
        "run_control": {"grace_window_hours": 20, "state_file": str(_gw_state_file)},
    }
    ts.run(_so_cfg, search_only=True, force=True)
    test(
        "run(): search_only does NOT persist run state",
        _mock_save_so.call_count == 0,
        f"save_run_state calls={_mock_save_so.call_count}",
    )

# CLI: --force mentioned in --help
_r_force = run_cli("--help")
test("CLI --help mentions --force", "--force" in _r_force.stdout)

# config file has required new keys
if CONFIG_FILE.exists():
    _disk_cfg2 = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    test("disk config has 'veto' section", "veto" in _disk_cfg2, f"keys={list(_disk_cfg2.keys())}")
    test(
        "disk config veto.require_domain_signals >= 1",
        int(_disk_cfg2.get("veto", {}).get("require_domain_signals", 0)) >= 1,
        str(_disk_cfg2.get("veto")),
    )
    test(
        "disk config veto.min_distinct_learnings >= 2",
        int(_disk_cfg2.get("veto", {}).get("min_distinct_learnings", 0)) >= 2,
        str(_disk_cfg2.get("veto")),
    )
    test("disk config has 'run_control' section", "run_control" in _disk_cfg2, f"keys={list(_disk_cfg2.keys())}")
    test(
        "disk config run_control.grace_window_hours >= 1",
        float(_disk_cfg2.get("run_control", {}).get("grace_window_hours", 0)) >= 1,
        str(_disk_cfg2.get("run_control")),
    )

# Workflow YAML has actions/cache step so the grace-window state file actually
# survives between GitHub-hosted Actions runner runs (otherwise ephemeral).
_workflow_path = REPO / ".github" / "workflows" / "trend-scout.yml"
if _workflow_path.exists():
    _wf_text = _workflow_path.read_text(encoding="utf-8")
    test(
        "workflow: includes actions/cache step for CI grace-window state persistence",
        "actions/cache" in _wf_text,
        "actions/cache not found in trend-scout.yml",
    )
    test(
        "workflow: cache path covers .trend-scout-state.json",
        ".trend-scout-state.json" in _wf_text,
        "state file path not found in trend-scout.yml",
    )
    test(
        "workflow: cache restore-keys uses 'trend-scout-state-' prefix for cross-run recall",
        "trend-scout-state-" in _wf_text,
        "trend-scout-state- prefix not found in trend-scout.yml",
    )
else:
    test("workflow file exists", False, f"{_workflow_path} not found")


# ─── Multi-lane discovery & jcode-class replay ────────────────────────────────

print("\n🛤  Multi-Lane Discovery (mocked)")

# jcode-class fixture: Rust coding-agent harness, 920+ stars, MCP, multi-session
JCODE_LIKE_REPO: dict = {
    "full_name": "example/jcode-like-agent",
    "name": "jcode-like-agent",
    "description": "Next-generation coding agent harness with memory, MCP, and multi-session support",
    "html_url": "https://github.com/example/jcode-like-agent",
    "created_at": "2024-01-01T00:00:00Z",
    "pushed_at": "2025-01-10T12:00:00Z",
    "stargazers_count": 920,
    "forks_count": 103,
    "watchers_count": 920,
    "open_issues_count": 15,
    "language": "Rust",
    "topics": ["coding-agent", "ai-coding", "mcp", "llm", "terminal"],
    "fork": False,
    "archived": False,
    "license": {"spdx_id": "MIT"},
}

# 1. jcode-class repo should score > 0 with multi-lane config (MCP topic included)
_ml_cfg = ts.load_config(None)  # includes adjacent-ai-dev lane from disk config
jcode_score = ts.score_repo(JCODE_LIKE_REPO, _ml_cfg)
test("jcode-class repo scores > 0 with multi-lane config", jcode_score > 0.0, f"score={jcode_score}")
test("jcode-class repo scores above primary-config minimum (0.15)", jcode_score >= 0.15, f"score={jcode_score}")

# 2. jcode-class repo should NOT be vetoed (it has MCP + memory signals in description)
_ml_veto, _ml_veto_reason = ts._should_veto_candidate(
    JCODE_LIKE_REPO,
    "Hybrid FTS+semantic retrieval and MCP server interface for coding agent memory.",
    _ml_cfg.get("search", {}).get("our_topics", []),
    {"require_domain_signals": 1, "min_distinct_learnings": 2},
)
test("jcode-class repo passes veto gate (domain signals present)", not _ml_veto, f"reason={_ml_veto_reason}")

# 3. Multi-lane search_stage: adjacent lane results pool into candidates
with (
    mock.patch.object(ts.GitHubClient, "search_repos", return_value=[]) as _mock_kw,
    mock.patch.object(ts.GitHubClient, "search_repos_by_topic", return_value=[JCODE_LIKE_REPO]) as _mock_topic,
    mock.patch("time.sleep", return_value=None),
):
    _lane_cfg = ts.load_config(None)
    # Primary lane has no topic matches for jcode; adjacent lane has mcp/coding-agent
    _lane_cfg["search"]["extra_topics"] = []
    _lane_cfg["search"]["seed_keywords"] = []
    _lane_cfg["lanes"] = [
        {
            "name": "adjacent-ai-dev",
            "keywords": [],
            "topics": ["coding-agent", "mcp"],
            "min_stars": 2,
            "max_per_query": 10,
            "lookback_days": 365,
            "language": None,
        }
    ]
    _raw_repos = ts.search_stage(ts.GitHubClient(token="ghp_test"), _lane_cfg)
    test(
        "multi-lane: adjacent lane results appear in raw candidates",
        any(r.get("full_name") == "example/jcode-like-agent" for r in _raw_repos),
        f"raw_repos={[r.get('full_name') for r in _raw_repos]}",
    )
    _jcode_in_raw = next((r for r in _raw_repos if r.get("full_name") == "example/jcode-like-agent"), None)
    if _jcode_in_raw:
        test(
            "multi-lane: adjacent lane repo tagged with correct lane",
            _jcode_in_raw.get("_discovery_lane") == "adjacent-ai-dev",
            f"lane={_jcode_in_raw.get('_discovery_lane')}",
        )
    else:
        test("multi-lane: adjacent lane repo tagged with correct lane", False, "not found in raw")

# 4. jcode-class repo survives shortlisting with adjacent-lane config
with (
    mock.patch.object(ts.GitHubClient, "search_repos", return_value=[]) as _mock_kw2,
    mock.patch.object(ts.GitHubClient, "search_repos_by_topic", return_value=[JCODE_LIKE_REPO]) as _mock_topic2,
    mock.patch("time.sleep", return_value=None),
):
    _shortlist_cfg = ts.load_config(None)
    _shortlist_cfg["search"]["extra_topics"] = []
    _shortlist_cfg["search"]["seed_keywords"] = []
    _shortlist_cfg["shortlist"]["min_score"] = 0.1
    _shortlist_cfg["shortlist"]["max_candidates"] = 5
    _shortlist_cfg["lanes"] = [
        {
            "name": "adjacent-ai-dev",
            "keywords": [],
            "topics": ["coding-agent"],
            "min_stars": 2,
            "max_per_query": 10,
            "lookback_days": 365,
            "language": None,
        }
    ]
    _raw2 = ts.search_stage(ts.GitHubClient(token="ghp_test"), _shortlist_cfg)
    _shortlisted2 = ts.shortlist_repos(_raw2, _shortlist_cfg)
    test(
        "multi-lane: jcode-class repo survives shortlisting",
        any(r.get("full_name") == "example/jcode-like-agent" for r in _shortlisted2),
        f"shortlisted={[r.get('full_name') for r in _shortlisted2]}",
    )

# 5. build_discovery_explain produces per-lane stats
_explain_repos = [
    {
        **JCODE_LIKE_REPO,
        "_discovery_lane": "adjacent-ai-dev",
        "_discovery_query": "coding-agent",
        "_lane_stats": [
            {"name": "primary", "keywords": [], "topics": [{"query": "ai-tools", "count": 0}]},
            {"name": "adjacent-ai-dev", "keywords": [], "topics": [{"query": "coding-agent", "count": 1}]},
        ],
    },
]
_explain_cfg = ts.load_config(None)
_explain = ts.build_discovery_explain(
    _explain_repos,
    _explain_repos,
    "2025-01-01T00:00:00+00:00",
    config=_explain_cfg,
)
test("build_discovery_explain has 'lanes' key", "lanes" in _explain)
test("build_discovery_explain has total_raw_candidates", _explain.get("total_raw_candidates") == 1)
test("build_discovery_explain has per-lane entries", len(_explain.get("lanes", [])) == 2)
test("build_discovery_explain has shortlisted entries", len(_explain.get("shortlisted", [])) == 1)
_adj_lane_explain = next((l for l in _explain["lanes"] if l["name"] == "adjacent-ai-dev"), None)
test(
    "build_discovery_explain adjacent lane has unique_new_repos=1",
    _adj_lane_explain is not None and _adj_lane_explain.get("unique_new_repos") == 1,
    str(_adj_lane_explain),
)
_expected_explain_score = ts.score_repo(
    JCODE_LIKE_REPO,
    _explain_cfg,
    term_set=ts._build_global_term_set(_explain_cfg),
)
test(
    "build_discovery_explain shortlisted score matches real scoring",
    _explain["shortlisted"][0].get("score") == _expected_explain_score,
    f"artifact={_explain['shortlisted'][0].get('score')} expected={_expected_explain_score}",
)
_fixture_goldset = {
    "path": str(GOLDSET_FILE),
    "entries": [
        {
            "repo": "example/jcode-like-agent",
            "required": True,
            "expected_lane": "adjacent-ai-dev",
            "min_score": 0.15,
            "category": "adjacent-coding-agent",
        }
    ],
}
_goldset_explain = ts.build_discovery_explain(
    _explain_repos,
    _explain_repos,
    "2025-01-01T00:00:00+00:00",
    config=_explain_cfg,
    goldset=_fixture_goldset,
)
test("build_discovery_explain includes goldset section", "goldset" in _goldset_explain)
test(
    "goldset marks jcode-like repo as shortlisted",
    _goldset_explain["goldset"]["entries"][0].get("status") == "shortlisted",
    str(_goldset_explain["goldset"]["entries"][0]),
)
test(
    "goldset lane expectation matches actual lane",
    _goldset_explain["goldset"]["entries"][0].get("lane_ok") is True,
    str(_goldset_explain["goldset"]["entries"][0]),
)
test(
    "goldset min_score expectation passes",
    _goldset_explain["goldset"]["entries"][0].get("score_ok") is True,
    str(_goldset_explain["goldset"]["entries"][0]),
)
_goldset_missing = ts.build_discovery_explain(
    [],
    [],
    "2025-01-01T00:00:00+00:00",
    config=_explain_cfg,
    goldset=_fixture_goldset,
)
test(
    "goldset missing repo increments required_missing",
    _goldset_missing["goldset"].get("required_missing") == 1,
    str(_goldset_missing["goldset"]),
)
test(
    "goldset missing repo marks lane_ok unknown instead of false",
    _goldset_missing["goldset"]["entries"][0].get("lane_ok") is None,
    str(_goldset_missing["goldset"]["entries"][0]),
)
test(
    "goldset missing repo marks score_ok unknown instead of false",
    _goldset_missing["goldset"]["entries"][0].get("score_ok") is None,
    str(_goldset_missing["goldset"]["entries"][0]),
)
test(
    "goldset missing repo does not inflate lane mismatch summary",
    _goldset_missing["goldset"].get("lane_mismatches") == 0,
    str(_goldset_missing["goldset"]),
)
test(
    "goldset missing repo does not inflate score failure summary",
    _goldset_missing["goldset"].get("score_failures") == 0,
    str(_goldset_missing["goldset"]),
)
_goldset_shortlisted_only = ts.build_discovery_explain(
    [],
    _explain_repos,
    "2025-01-01T00:00:00+00:00",
    config=_explain_cfg,
    goldset=_fixture_goldset,
)
test(
    "goldset shortlisted-only repo does not count as raw match",
    _goldset_shortlisted_only["goldset"].get("found_in_raw") == 0,
    str(_goldset_shortlisted_only["goldset"]),
)
test(
    "goldset shortlisted-only repo still counts as shortlist match",
    _goldset_shortlisted_only["goldset"].get("found_in_shortlist") == 1,
    str(_goldset_shortlisted_only["goldset"]),
)

# 6. Disk config has lanes section with at least one adjacent lane
_disk_cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
_disk_lanes = _disk_cfg.get("lanes", [])
test("disk config has 'lanes' section", "lanes" in _disk_cfg)
test("disk config lanes is a list", isinstance(_disk_lanes, list))
test("disk config has at least one additional lane", len(_disk_lanes) >= 1, f"found {len(_disk_lanes)} lanes")
if _disk_lanes:
    _first_lane = _disk_lanes[0]
    test("disk config lane has 'name' field", "name" in _first_lane)
    test("disk config lane has 'keywords' field", "keywords" in _first_lane)
    test("disk config lane has 'topics' field", "topics" in _first_lane)
    test(
        "disk config adjacent lane has language=null (any language)",
        _first_lane.get("language") is None,
        f"language={_first_lane.get('language')!r}",
    )
    test("disk config adjacent lane has min_stars", "min_stars" in _first_lane)
    _lane_keywords = _first_lane.get("keywords", [])
    test(
        "disk config adjacent lane includes exact harness query",
        '"Coding Agent Harness" in:name,description' in _lane_keywords,
        str(_lane_keywords),
    )
    test(
        "disk config adjacent lane includes topic-qualified coding-agent queries",
        "topic:cli topic:coding-agent" in _lane_keywords and "topic:tui topic:coding-agent" in _lane_keywords,
        str(_lane_keywords),
    )

# 7. Gold-set file exists and includes jcode
test("trend-scout-goldset.json exists", GOLDSET_FILE.exists())
if GOLDSET_FILE.exists():
    _goldset_disk = ts.load_goldset(GOLDSET_FILE)
    test("goldset file has entries", len(_goldset_disk.get("entries", [])) >= 1, str(_goldset_disk))
    test(
        "goldset includes jcode",
        any(entry.get("repo") == "1jehuang/jcode" for entry in _goldset_disk.get("entries", [])),
        str(_goldset_disk.get("entries")),
    )

# 8. CLI: --explain flag is present in help text
_explain_help_result = run_cli("--help")
test(
    "CLI --explain flag appears in --help",
    "--explain" in _explain_help_result.stdout,
    _explain_help_result.stdout[:300],
)

# 9. Workflow uploads explain artifacts for manual runs
test("trend-scout workflow exists", WORKFLOW_FILE.exists())
if WORKFLOW_FILE.exists():
    _workflow_text = WORKFLOW_FILE.read_text(encoding="utf-8")
    test(
        "workflow uploads explain artifact",
        "actions/upload-artifact" in _workflow_text and ".trend-scout-discovery-explain.json" in _workflow_text,
        _workflow_text,
    )

# 10. --explain writes artifact file (dry-run + search-only)
_explain_out = SCRATCH / "test-explain-output.json"
with (
    mock.patch.object(ts.GitHubClient, "search_repos", return_value=[]),
    mock.patch.object(ts.GitHubClient, "search_repos_by_topic", return_value=[]),
    mock.patch("time.sleep", return_value=None),
):
    _artifact_cfg = ts.load_config(None)
    _artifact_cfg["search"]["seed_keywords"] = []
    _artifact_cfg["search"]["extra_topics"] = []
    _artifact_cfg["lanes"] = []
    ts.run(
        _artifact_cfg,
        search_only=True,
        explain=True,
        explain_output=_explain_out,
    )
test("--explain: artifact file written", _explain_out.exists(), f"path={_explain_out}")
if _explain_out.exists():
    _artifact = json.loads(_explain_out.read_text(encoding="utf-8"))
    test("--explain artifact has run_at", "run_at" in _artifact)
    test("--explain artifact has lanes array", isinstance(_artifact.get("lanes"), list))
    test("--explain artifact has total_raw_candidates", "total_raw_candidates" in _artifact)
    test("--explain artifact includes goldset section when file exists", "goldset" in _artifact)

# 11. --explain preserves lane stats when discovery returns zero repos
_empty_lane_explain_out = SCRATCH / "test-explain-empty-lanes.json"
with (
    mock.patch.object(ts.GitHubClient, "search_repos", return_value=[]),
    mock.patch.object(ts.GitHubClient, "search_repos_by_topic", return_value=[]),
    mock.patch("time.sleep", return_value=None),
):
    _empty_lane_cfg = ts.load_config(None)
    _empty_lane_cfg["search"]["seed_keywords"] = []
    _empty_lane_cfg["search"]["extra_topics"] = ["knowledge-base"]
    _empty_lane_cfg["lanes"] = [
        {
            "name": "adjacent-ai-dev",
            "keywords": [],
            "topics": ["coding-agent"],
            "min_stars": 2,
            "language": None,
        }
    ]
    ts.run(
        _empty_lane_cfg,
        search_only=True,
        explain=True,
        explain_output=_empty_lane_explain_out,
    )
test(
    "--explain preserves lane stats on zero-result search",
    _empty_lane_explain_out.exists(),
    f"path={_empty_lane_explain_out}",
)
if _empty_lane_explain_out.exists():
    _empty_artifact = json.loads(_empty_lane_explain_out.read_text(encoding="utf-8"))
    _empty_lane_names = [lane.get("name") for lane in _empty_artifact.get("lanes", [])]
    test(
        "--explain zero-result artifact keeps primary + adjacent lane metadata",
        _empty_lane_names == ["primary", "adjacent-ai-dev"],
        str(_empty_lane_names),
    )
    test(
        "--explain zero-result artifact still evaluates goldset",
        "goldset" in _empty_artifact and _empty_artifact["goldset"].get("required_missing", 0) >= 1,
        str(_empty_artifact.get("goldset")),
    )

# 11b. --explain: not-shortlisted early-return path still writes artifact
_no_shortlist_explain_out = SCRATCH / "test-explain-no-shortlist.json"
with (
    mock.patch.object(ts.GitHubClient, "search_repos", return_value=[]),
    mock.patch.object(ts.GitHubClient, "search_repos_by_topic", return_value=[]),
    mock.patch("time.sleep", return_value=None),
):
    _ns_cfg = ts.load_config(None)
    _ns_cfg["search"]["seed_keywords"] = ["trend-scout-zero-match-xyz"]
    _ns_cfg["search"]["extra_topics"] = []
    _ns_cfg["lanes"] = []
    # No search_only → hits the "if not shortlisted" early-return path
    ts.run(
        _ns_cfg,
        search_only=False,
        explain=True,
        explain_output=_no_shortlist_explain_out,
    )
test(
    "--explain not-shortlisted path: artifact written",
    _no_shortlist_explain_out.exists(),
    f"path={_no_shortlist_explain_out}",
)
if _no_shortlist_explain_out.exists():
    _ns_artifact = json.loads(_no_shortlist_explain_out.read_text(encoding="utf-8"))
    test("--explain not-shortlisted artifact has run_at", "run_at" in _ns_artifact)
    test(
        "--explain not-shortlisted artifact total_raw_candidates == 0",
        _ns_artifact.get("total_raw_candidates") == 0,
        str(_ns_artifact.get("total_raw_candidates")),
    )
    test(
        "--explain not-shortlisted artifact shortlisted is empty list",
        _ns_artifact.get("shortlisted") == [],
        str(_ns_artifact.get("shortlisted")),
    )

# 11c. --explain: grace-window early-return path still writes artifact
_grace_explain_out = SCRATCH / "test-explain-grace-window.json"
_grace_cfg = ts.load_config(None)
_grace_cfg["run_control"] = {"grace_window_hours": 48}
_recent_run_state = {"last_run_utc": ts.datetime.now(ts.timezone.utc).isoformat()}
with (
    mock.patch.object(ts, "load_run_state", return_value=_recent_run_state),
    mock.patch("time.sleep", return_value=None),
):
    ts.run(
        _grace_cfg,
        force=False,
        explain=True,
        explain_output=_grace_explain_out,
    )
test("--explain grace-window path: artifact written", _grace_explain_out.exists(), f"path={_grace_explain_out}")
if _grace_explain_out.exists():
    _gw_artifact = json.loads(_grace_explain_out.read_text(encoding="utf-8"))
    test("--explain grace-window artifact has run_at", "run_at" in _gw_artifact)
    test(
        "--explain grace-window artifact total_raw_candidates == 0",
        _gw_artifact.get("total_raw_candidates") == 0,
        str(_gw_artifact.get("total_raw_candidates")),
    )
    test(
        "--explain grace-window artifact shortlisted is empty list",
        _gw_artifact.get("shortlisted") == [],
        str(_gw_artifact.get("shortlisted")),
    )
    # Regression: grace-window skip must not emit false goldset_misses
    test(
        "--explain grace-window artifact goldset_misses is empty (no false failures)",
        _gw_artifact.get("goldset_misses") == [],
        f"goldset_misses={_gw_artifact.get('goldset_misses')}",
    )
    test(
        "--explain grace-window artifact has run_skipped=True",
        _gw_artifact.get("run_skipped") is True,
        f"run_skipped={_gw_artifact.get('run_skipped')}",
    )
    test(
        "--explain grace-window artifact has non-empty skip_reason",
        bool(_gw_artifact.get("skip_reason")),
        f"skip_reason={_gw_artifact.get('skip_reason')!r}",
    )

# 11c-unit. build_discovery_explain with skip_reason: direct unit coverage
_skip_goldset = {"entries": [{"repo": "some-org/some-repo", "required": True}]}
_skip_artifact = ts.build_discovery_explain(
    [],
    [],
    "2024-01-01T00:00:00+00:00",
    config=None,
    goldset=_skip_goldset,
    skip_reason="last run 5.0h ago, grace window 48h (43.0h remaining)",
)
test(
    "build_discovery_explain skip_reason → run_skipped=True",
    _skip_artifact.get("run_skipped") is True,
    str(_skip_artifact.get("run_skipped")),
)
test(
    "build_discovery_explain skip_reason → skip_reason preserved in artifact",
    "grace window 48h" in str(_skip_artifact.get("skip_reason", "")),
    f"skip_reason={_skip_artifact.get('skip_reason')!r}",
)
test(
    "build_discovery_explain skip_reason → goldset_misses is [] (no false raw_miss)",
    _skip_artifact.get("goldset_misses") == [],
    f"goldset_misses={_skip_artifact.get('goldset_misses')}",
)
test(
    "build_discovery_explain skip_reason → goldset block absent (not computed for skipped run)",
    "goldset" not in _skip_artifact,
    f"goldset keys: {list(_skip_artifact.keys())}",
)

# 12. cross-lane term set: shortlist_repos uses keywords from all lanes
_cross_cfg = ts.load_config(None)
_cross_cfg["search"]["seed_keywords"] = ["python fts knowledge"]
_cross_cfg["lanes"] = [{"name": "adj", "keywords": ["coding agent rust mcp"], "topics": []}]
# jcode-like repo has 'coding' and 'agent' in name/desc → should match cross-lane terms
_cross_terms_jcode_score = ts.score_repo(
    JCODE_LIKE_REPO,
    _cross_cfg,
    term_set=ts._build_term_set(["coding agent rust mcp"]),
)
test(
    "cross-lane term set: jcode-like repo scores higher with adjacent keywords",
    _cross_terms_jcode_score > 0,
    f"score={_cross_terms_jcode_score}",
)


# 13. disk config keyword qualifiers can surface jcode-class repos via search_stage
def _mock_keyword_search(query, *, min_stars=0, max_results=10, created_after=None, language=None):
    if query in {
        '"Coding Agent Harness" in:name,description',
        "topic:cli topic:coding-agent",
        "topic:tui topic:coding-agent",
    }:
        return [JCODE_LIKE_REPO]
    return []


with (
    mock.patch.object(ts.GitHubClient, "search_repos", side_effect=_mock_keyword_search),
    mock.patch.object(ts.GitHubClient, "search_repos_by_topic", return_value=[]),
    mock.patch("time.sleep", return_value=None),
):
    _disk_query_cfg = ts.load_config(None)
    _disk_query_cfg["search"]["seed_keywords"] = []
    _disk_query_cfg["search"]["extra_topics"] = []
    _raw_disk = ts.search_stage(ts.GitHubClient(token="ghp_test"), _disk_query_cfg)
    _jcode_disk = next((r for r in _raw_disk if r.get("full_name") == "example/jcode-like-agent"), None)
    test(
        "disk config qualifier keyword queries surface jcode-like repo",
        _jcode_disk is not None,
        str([r.get("full_name") for r in _raw_disk]),
    )
    if _jcode_disk is not None:
        test(
            "disk config qualifier query preserves keyword provenance",
            _jcode_disk.get("_discovery_query")
            in {
                '"Coding Agent Harness" in:name,description',
                "topic:cli topic:coding-agent",
                "topic:tui topic:coding-agent",
            },
            str(_jcode_disk),
        )


# ─── Token-Efficiency-CLI Lane (RTK-class) ────────────────────────────────────

print("\n🛤  Token-Efficiency-CLI Lane (RTK-class)")

# A. Disk config has token-efficiency-cli lane
_disk_cfg_rtk = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
_rtk_lane = next(
    (l for l in _disk_cfg_rtk.get("lanes", []) if l.get("name") == "token-efficiency-cli"),
    None,
)
test(
    "disk config has token-efficiency-cli lane",
    _rtk_lane is not None,
    f"lanes={[l.get('name') for l in _disk_cfg_rtk.get('lanes', [])]}",
)
if _rtk_lane:
    test(
        "token-efficiency-cli lane is language-agnostic (language=null)",
        _rtk_lane.get("language") is None,
        f"language={_rtk_lane.get('language')!r}",
    )
    test(
        "token-efficiency-cli lane has keywords", len(_rtk_lane.get("keywords", [])) > 0, str(_rtk_lane.get("keywords"))
    )
    test("token-efficiency-cli lane has topics", len(_rtk_lane.get("topics", [])) > 0, str(_rtk_lane.get("topics")))
    test(
        "token-efficiency-cli lane includes rtk name qualifier",
        any("rtk" in str(k).lower() for k in _rtk_lane.get("keywords", [])),
        str(_rtk_lane.get("keywords")),
    )

# B. Goldset includes rtk-ai/rtk
_goldset_disk_rtk = ts.load_goldset(GOLDSET_FILE)
_rtk_entry = next(
    (e for e in _goldset_disk_rtk.get("entries", []) if e.get("repo") == "rtk-ai/rtk"),
    None,
)
test("goldset includes rtk-ai/rtk", _rtk_entry is not None, str(_goldset_disk_rtk.get("entries")))
if _rtk_entry:
    test(
        "rtk-ai/rtk goldset expected_lane is token-efficiency-cli",
        _rtk_entry.get("expected_lane") == "token-efficiency-cli",
        str(_rtk_entry),
    )
    test("rtk-ai/rtk goldset entry is required", bool(_rtk_entry.get("required", True)), str(_rtk_entry))

# C. build_discovery_explain produces goldset_misses top-level key (raw miss)
_rtk_explain_no_raw = ts.build_discovery_explain(
    [],
    [],
    "2025-01-01T00:00:00+00:00",
    config=ts.load_config(None),
    goldset={
        "path": "synthetic",
        "entries": [
            {
                "repo": "rtk-ai/rtk",
                "required": True,
                "expected_lane": "token-efficiency-cli",
                "min_score": 0.1,
            }
        ],
    },
)
test("build_discovery_explain has goldset_misses top-level key", "goldset_misses" in _rtk_explain_no_raw)
test("goldset_misses is a list", isinstance(_rtk_explain_no_raw.get("goldset_misses"), list))
test(
    "goldset_misses has 1 entry for raw miss",
    len(_rtk_explain_no_raw.get("goldset_misses", [])) == 1,
    str(_rtk_explain_no_raw.get("goldset_misses")),
)
_miss_row = _rtk_explain_no_raw["goldset_misses"][0] if _rtk_explain_no_raw.get("goldset_misses") else {}
test(
    "goldset_miss row has all required fields",
    all(k in _miss_row for k in ("repo", "required", "expected_lane", "found_in_raw", "found_in_shortlist", "reason")),
    str(list(_miss_row.keys())),
)
test("goldset_miss found_in_raw=False for raw miss", _miss_row.get("found_in_raw") is False, str(_miss_row))
test("goldset_miss reason mentions raw_miss", "raw_miss" in str(_miss_row.get("reason", "")), str(_miss_row))

# D. RTK-class repo fixture surfaced by token-efficiency-cli lane via mocked search
RTK_LIKE_REPO: dict = {
    "full_name": "rtk-ai/rtk",
    "name": "rtk",
    "description": "RTK: token efficiency streaming CLI for LLM context management",
    "html_url": "https://github.com/rtk-ai/rtk",
    "created_at": "2024-06-01T00:00:00Z",
    "pushed_at": "2025-01-05T12:00:00Z",
    "stargazers_count": 45,
    "forks_count": 5,
    "watchers_count": 45,
    "open_issues_count": 3,
    "language": "TypeScript",
    "topics": ["cli", "token-efficiency", "llm", "streaming"],
    "fork": False,
    "archived": False,
    "license": {"spdx_id": "MIT"},
}


def _mock_rtk_kw(query, *, min_stars=0, max_results=10, created_after=None, language=None):
    if "rtk" in str(query).lower() or "token" in str(query).lower():
        return [RTK_LIKE_REPO]
    return []


def _mock_rtk_topic(topic, *, min_stars=0, max_results=10, language=None):
    if topic in ("token-efficiency", "cli", "streaming"):
        return [RTK_LIKE_REPO]
    return []


with (
    mock.patch.object(ts.GitHubClient, "search_repos", side_effect=_mock_rtk_kw),
    mock.patch.object(ts.GitHubClient, "search_repos_by_topic", side_effect=_mock_rtk_topic),
    mock.patch("time.sleep", return_value=None),
):
    _rtk_cfg = ts.load_config(None)
    _rtk_cfg["search"]["seed_keywords"] = []
    _rtk_cfg["search"]["extra_topics"] = []
    _rtk_cfg["lanes"] = [
        {
            "name": "token-efficiency-cli",
            "keywords": ['"rtk" in:name,description'],
            "topics": ["token-efficiency"],
            "min_stars": 2,
            "max_per_query": 10,
            "lookback_days": 365,
            "language": None,
        }
    ]
    _raw_rtk = ts.search_stage(ts.GitHubClient(token="ghp_test"), _rtk_cfg)
    test(
        "token-efficiency-cli lane surfaces RTK-class repo via mocked search",
        any(r.get("full_name") == "rtk-ai/rtk" for r in _raw_rtk),
        str([r.get("full_name") for r in _raw_rtk]),
    )
    _rtk_in_raw = next((r for r in _raw_rtk if r.get("full_name") == "rtk-ai/rtk"), None)
    if _rtk_in_raw:
        test(
            "RTK-class repo tagged with token-efficiency-cli discovery lane",
            _rtk_in_raw.get("_discovery_lane") == "token-efficiency-cli",
            str(_rtk_in_raw.get("_discovery_lane")),
        )
    else:
        test("RTK-class repo tagged with token-efficiency-cli discovery lane", False, "not found in raw")

# E. goldset_misses empty when RTK repo found in raw with correct lane
_rtk_explain_with_raw = ts.build_discovery_explain(
    [{**RTK_LIKE_REPO, "_discovery_lane": "token-efficiency-cli", "_discovery_query": '"rtk" in:name,description'}],
    [],
    "2025-01-01T00:00:00+00:00",
    config=ts.load_config(None),
    goldset={
        "path": "synthetic",
        "entries": [
            {"repo": "rtk-ai/rtk", "required": True, "expected_lane": "token-efficiency-cli", "min_score": 0.0}
        ],
    },
)
test(
    "goldset_misses empty when RTK-class repo found in raw with correct lane",
    len(_rtk_explain_with_raw.get("goldset_misses", [])) == 0,
    str(_rtk_explain_with_raw.get("goldset_misses")),
)

# F. goldset_misses captures lane mismatch (found in raw, but wrong lane)
_rtk_explain_wrong_lane = ts.build_discovery_explain(
    [{**RTK_LIKE_REPO, "_discovery_lane": "primary", "_discovery_query": "ai tools"}],
    [],
    "2025-01-01T00:00:00+00:00",
    config=ts.load_config(None),
    goldset={
        "path": "synthetic",
        "entries": [
            {"repo": "rtk-ai/rtk", "required": True, "expected_lane": "token-efficiency-cli", "min_score": 0.0}
        ],
    },
)
test(
    "goldset_misses captures lane mismatch (found in raw but wrong lane)",
    len(_rtk_explain_wrong_lane.get("goldset_misses", [])) == 1,
    str(_rtk_explain_wrong_lane.get("goldset_misses")),
)
_lane_miss_row = _rtk_explain_wrong_lane["goldset_misses"][0] if _rtk_explain_wrong_lane.get("goldset_misses") else {}
test("goldset_miss lane_miss: found_in_raw=True", _lane_miss_row.get("found_in_raw") is True, str(_lane_miss_row))
test(
    "goldset_miss lane_miss: reason mentions lane_miss",
    "lane_miss" in str(_lane_miss_row.get("reason", "")),
    str(_lane_miss_row),
)

# G. goldset_misses is empty list when goldset has no entries (graceful no-op)
_empty_goldset_explain = ts.build_discovery_explain(
    [],
    [],
    "2025-01-01T00:00:00+00:00",
    config=ts.load_config(None),
    goldset={"path": "synthetic", "entries": []},
)
test(
    "goldset_misses is empty list when goldset has no entries",
    _empty_goldset_explain.get("goldset_misses") == [],
    str(_empty_goldset_explain.get("goldset_misses")),
)

# H. Adjacent jcode lane regression: jcode still found after adding token-efficiency-cli
with (
    mock.patch.object(ts.GitHubClient, "search_repos", return_value=[]) as _mock_kw_reg,
    mock.patch.object(
        ts.GitHubClient,
        "search_repos_by_topic",
        side_effect=lambda t, **kw: [JCODE_LIKE_REPO] if t in ("coding-agent", "mcp") else [],
    ) as _mock_t_reg,
    mock.patch("time.sleep", return_value=None),
):
    _reg_cfg = ts.load_config(None)  # loads disk config with both lanes
    _reg_cfg["search"]["seed_keywords"] = []
    _reg_cfg["search"]["extra_topics"] = []
    _reg_raw = ts.search_stage(ts.GitHubClient(token="ghp_test"), _reg_cfg)
    test(
        "adjacent-ai-dev jcode regression: jcode still found with both lanes active",
        any(r.get("full_name") == "example/jcode-like-agent" for r in _reg_raw),
        str([r.get("full_name") for r in _reg_raw]),
    )
    _jcode_reg = next((r for r in _reg_raw if r.get("full_name") == "example/jcode-like-agent"), None)
    if _jcode_reg:
        test(
            "adjacent-ai-dev jcode regression: jcode lane tag unchanged",
            _jcode_reg.get("_discovery_lane") == "adjacent-ai-dev",
            str(_jcode_reg.get("_discovery_lane")),
        )
    else:
        test("adjacent-ai-dev jcode regression: jcode lane tag unchanged", False, "jcode not found in raw")

# I. Workflow always uploads explain artifact (not only on workflow_dispatch+explain)
if WORKFLOW_FILE.exists():
    _wf_rtk = WORKFLOW_FILE.read_text(encoding="utf-8")
    test(
        "workflow upload condition covers scheduled runs (always())",
        "if: ${{ always() }}" in _wf_rtk or "always()" in _wf_rtk,
        _wf_rtk[_wf_rtk.find("Upload discovery") : _wf_rtk.find("Upload discovery") + 200]
        if "Upload discovery" in _wf_rtk
        else _wf_rtk[-200:],
    )
else:
    test("workflow file exists for RTK lane checks", False, str(WORKFLOW_FILE))


# ─── Research Pack ────────────────────────────────────────────────────────────

print("\n📦 Research Pack")

# 1. build_research_pack_entry: has all required schema fields
_rp_cfg = ts.load_config(None)
_rp_repo = {
    **REPO_FIXTURE,
    "_discovery_lane": "primary",
    "_discovery_query": "AI coding session sqlite",
}
_rp_entry = ts.build_research_pack_entry(_rp_repo, "A Python knowledge base", _rp_cfg)
_REQUIRED_PACK_FIELDS = (
    "full_name",
    "html_url",
    "discovery_lane",
    "discovery_query",
    "score",
    "stars",
    "language",
    "topics",
    "why_discovered",
    "novelty_signals",
    "risk_signals",
    "recommended_followups",
    "tentacle_handoff",
)
test(
    "research_pack_entry has all required fields",
    all(f in _rp_entry for f in _REQUIRED_PACK_FIELDS),
    str([f for f in _REQUIRED_PACK_FIELDS if f not in _rp_entry]),
)
test("research_pack_entry.full_name matches repo", _rp_entry["full_name"] == REPO_FIXTURE["full_name"])
test("research_pack_entry.score is float", isinstance(_rp_entry["score"], float))
test("research_pack_entry.stars matches repo", _rp_entry["stars"] == REPO_FIXTURE["stargazers_count"])
test("research_pack_entry.discovery_lane preserved", _rp_entry["discovery_lane"] == "primary")
test(
    "research_pack_entry.why_discovered is non-empty list",
    isinstance(_rp_entry["why_discovered"], list) and len(_rp_entry["why_discovered"]) > 0,
)
test(
    "research_pack_entry.novelty_signals is non-empty list",
    isinstance(_rp_entry["novelty_signals"], list) and len(_rp_entry["novelty_signals"]) > 0,
)
test(
    "research_pack_entry.risk_signals is non-empty list",
    isinstance(_rp_entry["risk_signals"], list) and len(_rp_entry["risk_signals"]) > 0,
)
test(
    "research_pack_entry.recommended_followups is non-empty list",
    isinstance(_rp_entry["recommended_followups"], list) and len(_rp_entry["recommended_followups"]) > 0,
)
test(
    "research_pack_entry.tentacle_handoff is non-empty string",
    isinstance(_rp_entry["tentacle_handoff"], str) and len(_rp_entry["tentacle_handoff"]) > 0,
)
test("research_pack_entry.topics is a list", isinstance(_rp_entry["topics"], list))

# 2. build_research_pack: schema_version=1, source, generated_at, repos list
_rp_pack = ts.build_research_pack([(_rp_repo, "readme text")], _rp_cfg)
test("research_pack has schema_version=1", _rp_pack.get("schema_version") == 1)
test("research_pack has source=trend-scout.py", _rp_pack.get("source") == "trend-scout.py")
test("research_pack has generated_at", bool(_rp_pack.get("generated_at")))
test("research_pack has repos list", isinstance(_rp_pack.get("repos"), list))
test("research_pack repos has 1 entry for 1 enriched repo", len(_rp_pack.get("repos", [])) == 1)
test("research_pack does not have run_skipped when not skipped", "run_skipped" not in _rp_pack)

# 3. build_research_pack: skipped run → run_skipped=True, repos=[], skip_reason preserved
_rp_skip = ts.build_research_pack([], _rp_cfg, skip_reason="last run 2.0h ago, grace window 20h (18.0h remaining)")
test("skipped research_pack has run_skipped=True", _rp_skip.get("run_skipped") is True)
test("skipped research_pack has repos=[]", _rp_skip.get("repos") == [])
test("skipped research_pack preserves skip_reason", "grace window 20h" in str(_rp_skip.get("skip_reason", "")))
test("skipped research_pack has schema_version=1", _rp_skip.get("schema_version") == 1)

# 4. build_research_pack with empty enriched list → empty repos, no skip_reason
_rp_empty = ts.build_research_pack([], _rp_cfg)
test("empty enriched → repos=[]", _rp_empty.get("repos") == [])
test("empty enriched (no skip) → no run_skipped key", "run_skipped" not in _rp_empty)

# 5. _write_research_pack_artifact: writes valid JSON file
_rp_out = SCRATCH / "test-research-pack.json"
ts._write_research_pack_artifact([(_rp_repo, "readme")], _rp_cfg, output=_rp_out)
test("_write_research_pack_artifact creates file", _rp_out.exists())
if _rp_out.exists():
    _rp_loaded = json.loads(_rp_out.read_text(encoding="utf-8"))
    test("written research pack is valid JSON with schema_version=1", _rp_loaded.get("schema_version") == 1)
    test("written research pack has repos list", isinstance(_rp_loaded.get("repos"), list))

# 6. _write_research_pack_artifact: skipped run
_rp_skip_out = SCRATCH / "test-research-pack-skip.json"
ts._write_research_pack_artifact([], _rp_cfg, output=_rp_skip_out, skip_reason="grace window 48h active")
test("_write_research_pack_artifact (skip) creates file", _rp_skip_out.exists())
if _rp_skip_out.exists():
    _rp_skip_loaded = json.loads(_rp_skip_out.read_text(encoding="utf-8"))
    test("written skipped pack has run_skipped=True", _rp_skip_loaded.get("run_skipped") is True)
    test("written skipped pack has repos=[]", _rp_skip_loaded.get("repos") == [])

# 7. CLI --research-pack flag appears in --help
_r_rp_help = run_cli("--help")
test("CLI --help mentions --research-pack", "--research-pack" in _r_rp_help.stdout)

# 8. run() --search-only + --research-pack: deterministic in-process, full output assertions
_rp_cli_out = SCRATCH / "cli-research-pack.json"
_rp_cli_repo = {**REPO_FIXTURE, "_discovery_lane": "primary", "_discovery_query": "ai sqlite"}
with (
    mock.patch.object(ts, "search_stage", return_value=[_rp_cli_repo]),
    mock.patch.object(ts, "shortlist_repos", return_value=[_rp_cli_repo]),
):
    _rp_cli_cfg = ts.load_config(None)
    _rp_cli_cfg["run_control"] = {"grace_window_hours": 0}
    _rp_cli_exit = ts.run(
        _rp_cli_cfg,
        search_only=True,
        dry_run=True,
        research_pack=True,
        research_pack_output=_rp_cli_out,
    )
test("run() --research-pack in-process → exit 0", _rp_cli_exit == 0, f"exit={_rp_cli_exit}")
test("run() --research-pack in-process → output file exists", _rp_cli_out.exists(), f"file={_rp_cli_out}")
if _rp_cli_out.exists():
    _rp_cli_loaded = json.loads(_rp_cli_out.read_text(encoding="utf-8"))
    test(
        "run() --research-pack output has schema_version=1",
        _rp_cli_loaded.get("schema_version") == 1,
        f"got={_rp_cli_loaded.get('schema_version')}",
    )
    test(
        "run() --research-pack output has repos list",
        isinstance(_rp_cli_loaded.get("repos"), list),
        f"repos={_rp_cli_loaded.get('repos')!r}",
    )
    test(
        "run() --research-pack output repos contains expected repo",
        any(r.get("full_name") == REPO_FIXTURE["full_name"] for r in _rp_cli_loaded.get("repos", [])),
        f"repos full_names={[r.get('full_name') for r in _rp_cli_loaded.get('repos', [])]}",
    )

# 9. run_skipped grace scenario via run() with research_pack=True
_rp_gw_out = SCRATCH / "gw-research-pack.json"
_rp_recent_ts = (
    __import__("datetime").datetime.now(__import__("datetime").timezone.utc) - __import__("datetime").timedelta(hours=2)
).isoformat()
with (
    mock.patch.object(ts, "search_stage", return_value=[]) as _rp_mock_search,
    mock.patch.object(ts, "load_run_state", return_value={"last_run_utc": _rp_recent_ts}),
):
    _rp_gw_cfg = {
        **ts.load_config(None),
        "run_control": {"grace_window_hours": 20},
    }
    _rp_gw_exit = ts.run(
        _rp_gw_cfg,
        dry_run=True,
        force=False,
        research_pack=True,
        research_pack_output=_rp_gw_out,
    )
test("run() grace-window skip with research_pack=True → exit 0", _rp_gw_exit == 0)
test("run() grace-window skip with research_pack=True → writes pack", _rp_gw_out.exists(), f"file={_rp_gw_out}")
if _rp_gw_out.exists():
    _rp_gw_loaded = json.loads(_rp_gw_out.read_text(encoding="utf-8"))
    test("grace-window pack has run_skipped=True", _rp_gw_loaded.get("run_skipped") is True)
    test("grace-window pack has schema_version=1", _rp_gw_loaded.get("schema_version") == 1)
    test("grace-window pack has repos=[]", _rp_gw_loaded.get("repos") == [])

# 10. run() search_only + research_pack writes pack from shortlist
_rp_so_out = SCRATCH / "so-research-pack.json"
with (
    mock.patch.object(
        ts,
        "search_stage",
        return_value=[{**REPO_FIXTURE, "_discovery_lane": "primary", "_discovery_query": "ai tools"}],
    ),
    mock.patch.object(
        ts,
        "shortlist_repos",
        return_value=[{**REPO_FIXTURE, "_discovery_lane": "primary", "_discovery_query": "ai tools"}],
    ),
):
    _rp_so_cfg = ts.load_config(None)
    _rp_so_cfg["run_control"] = {"grace_window_hours": 0}
    _rp_so_exit = ts.run(
        _rp_so_cfg,
        search_only=True,
        research_pack=True,
        research_pack_output=_rp_so_out,
    )
test("run() search_only + research_pack → exit 0", _rp_so_exit == 0)
test("run() search_only + research_pack → writes pack", _rp_so_out.exists(), f"file={_rp_so_out}")
if _rp_so_out.exists():
    _rp_so_loaded = json.loads(_rp_so_out.read_text(encoding="utf-8"))
    test("search_only pack has schema_version=1", _rp_so_loaded.get("schema_version") == 1)
    test("search_only pack has repos list", isinstance(_rp_so_loaded.get("repos"), list))


# ─── Claude-Code-Skills Lane (caveman-class) ─────────────────────────────────

print("\n🦴 Claude-Code-Skills Lane (caveman-class)")

# A. Disk config has claude-code-skills lane
_disk_cfg_ccs = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
_ccs_lane = next(
    (l for l in _disk_cfg_ccs.get("lanes", []) if l.get("name") == "claude-code-skills"),
    None,
)
test(
    "disk config has claude-code-skills lane",
    _ccs_lane is not None,
    f"lanes={[l.get('name') for l in _disk_cfg_ccs.get('lanes', [])]}",
)
if _ccs_lane:
    test(
        "claude-code-skills lane is language-agnostic (language=null)",
        _ccs_lane.get("language") is None,
        f"language={_ccs_lane.get('language')!r}",
    )
    test(
        "claude-code-skills lane has keywords",
        len(_ccs_lane.get("keywords", [])) > 0,
        str(_ccs_lane.get("keywords")),
    )
    test(
        "claude-code-skills lane has topics",
        len(_ccs_lane.get("topics", [])) > 0,
        str(_ccs_lane.get("topics")),
    )
    _ccs_topics = _ccs_lane.get("topics", [])
    for _required_topic in ("claude-code", "skill", "prompt-engineering", "anthropic", "claude"):
        test(
            f"claude-code-skills lane topics include '{_required_topic}'",
            _required_topic in _ccs_topics,
            str(_ccs_topics),
        )
    test(
        "claude-code-skills lane keywords mention claude-code or skill",
        any("claude" in str(k).lower() or "skill" in str(k).lower() for k in _ccs_lane.get("keywords", [])),
        str(_ccs_lane.get("keywords")),
    )

# B. Goldset includes JuliusBrussee/caveman
_goldset_disk_ccs = ts.load_goldset(GOLDSET_FILE)
_caveman_entry = next(
    (e for e in _goldset_disk_ccs.get("entries", []) if e.get("repo") == "JuliusBrussee/caveman"),
    None,
)
test("goldset includes JuliusBrussee/caveman", _caveman_entry is not None, str(_goldset_disk_ccs.get("entries")))
if _caveman_entry:
    test(
        "caveman goldset expected_lane is claude-code-skills",
        _caveman_entry.get("expected_lane") == "claude-code-skills",
        str(_caveman_entry),
    )
    test("caveman goldset entry is required", bool(_caveman_entry.get("required", True)), str(_caveman_entry))
    test(
        "caveman goldset entry has category claude-code-skills",
        _caveman_entry.get("category") == "claude-code-skills",
        str(_caveman_entry),
    )
    test(
        "caveman goldset notes mention discovery gap",
        "gap" in str(_caveman_entry.get("notes", "")).lower()
        or "vocabulary" in str(_caveman_entry.get("notes", "")).lower(),
        str(_caveman_entry.get("notes")),
    )

# C. Caveman-class repo fixture definition
CAVEMAN_LIKE_REPO: dict = {
    "full_name": "JuliusBrussee/caveman",
    "name": "caveman",
    "description": "Claude Code skill: caveman communication style to reduce token consumption",
    "html_url": "https://github.com/JuliusBrussee/caveman",
    "created_at": "2025-01-15T10:00:00Z",
    "pushed_at": "2025-04-01T12:00:00Z",
    "stargazers_count": 18,
    "forks_count": 2,
    "watchers_count": 18,
    "open_issues_count": 1,
    "language": None,
    "topics": ["claude-code", "skill", "tokens", "prompt-engineering", "anthropic", "claude"],
    "fork": False,
    "archived": False,
    "license": {"spdx_id": "MIT"},
}


def _mock_ccs_kw(query, *, min_stars=0, max_results=10, created_after=None, language=None):
    _q = str(query).lower()
    if "claude" in _q or "skill" in _q or "anthropic" in _q or "prompt" in _q:
        return [CAVEMAN_LIKE_REPO]
    return []


def _mock_ccs_topic(topic, *, min_stars=0, max_results=10, language=None):
    if topic in ("claude-code", "skill", "prompt-engineering", "anthropic", "claude", "tokens"):
        return [CAVEMAN_LIKE_REPO]
    return []


# D. claude-code-skills lane surfaces caveman-class repo via mocked search
with (
    mock.patch.object(ts.GitHubClient, "search_repos", side_effect=_mock_ccs_kw),
    mock.patch.object(ts.GitHubClient, "search_repos_by_topic", side_effect=_mock_ccs_topic),
    mock.patch("time.sleep", return_value=None),
):
    _ccs_cfg = ts.load_config(None)
    _ccs_cfg["search"]["seed_keywords"] = []
    _ccs_cfg["search"]["extra_topics"] = []
    _ccs_cfg["lanes"] = [
        {
            "name": "claude-code-skills",
            "keywords": ["topic:claude-code topic:skill"],
            "topics": ["claude-code", "skill"],
            "min_stars": 2,
            "max_per_query": 10,
            "lookback_days": 365,
            "language": None,
        }
    ]
    _raw_ccs = ts.search_stage(ts.GitHubClient(token="ghp_test"), _ccs_cfg)
    test(
        "claude-code-skills lane surfaces caveman-class repo via mocked search",
        any(r.get("full_name") == "JuliusBrussee/caveman" for r in _raw_ccs),
        str([r.get("full_name") for r in _raw_ccs]),
    )
    _caveman_in_raw = next((r for r in _raw_ccs if r.get("full_name") == "JuliusBrussee/caveman"), None)
    if _caveman_in_raw:
        test(
            "caveman-class repo tagged with claude-code-skills discovery lane",
            _caveman_in_raw.get("_discovery_lane") == "claude-code-skills",
            str(_caveman_in_raw.get("_discovery_lane")),
        )
    else:
        test("caveman-class repo tagged with claude-code-skills discovery lane", False, "not found in raw")

# E. Caveman-class repo scores > 0 with claude-code-skills lane config
_ccs_score_cfg = ts.load_config(None)
_ccs_score_cfg["lanes"] = [
    {
        "name": "claude-code-skills",
        "keywords": ["claude code skill"],
        "topics": ["claude-code", "skill", "prompt-engineering", "anthropic", "claude", "tokens"],
        "min_stars": 2,
        "max_per_query": 10,
        "lookback_days": 365,
        "language": None,
    }
]
_caveman_score = ts.score_repo(CAVEMAN_LIKE_REPO, _ccs_score_cfg)
test(
    "caveman-class repo scores > 0 with claude-code-skills lane config",
    _caveman_score > 0.0,
    f"score={_caveman_score}",
)

# F. goldset_misses captures raw miss for caveman (synthetic no-raw case)
_ccs_explain_no_raw = ts.build_discovery_explain(
    [],
    [],
    "2025-01-01T00:00:00+00:00",
    config=ts.load_config(None),
    goldset={
        "path": "synthetic",
        "entries": [
            {
                "repo": "JuliusBrussee/caveman",
                "required": True,
                "expected_lane": "claude-code-skills",
                "min_score": 0.1,
            }
        ],
    },
)
test(
    "goldset_misses captures raw miss for caveman",
    len(_ccs_explain_no_raw.get("goldset_misses", [])) == 1,
    str(_ccs_explain_no_raw.get("goldset_misses")),
)
_ccs_miss_row = _ccs_explain_no_raw["goldset_misses"][0] if _ccs_explain_no_raw.get("goldset_misses") else {}
test(
    "caveman goldset_miss reason mentions raw_miss",
    "raw_miss" in str(_ccs_miss_row.get("reason", "")),
    str(_ccs_miss_row),
)

# G. goldset_misses empty when caveman found in raw with correct lane
_ccs_explain_found = ts.build_discovery_explain(
    [{**CAVEMAN_LIKE_REPO, "_discovery_lane": "claude-code-skills", "_discovery_query": "topic:claude-code topic:skill"}],
    [],
    "2025-01-01T00:00:00+00:00",
    config=ts.load_config(None),
    goldset={
        "path": "synthetic",
        "entries": [
            {"repo": "JuliusBrussee/caveman", "required": True, "expected_lane": "claude-code-skills", "min_score": 0.0}
        ],
    },
)
test(
    "goldset_misses empty when caveman found in raw with correct lane",
    len(_ccs_explain_found.get("goldset_misses", [])) == 0,
    str(_ccs_explain_found.get("goldset_misses")),
)

# H. Regression: existing lanes not displaced — adjacent-ai-dev and token-efficiency-cli still present
_ccs_all_lanes = [l.get("name") for l in _disk_cfg_ccs.get("lanes", [])]
for _existing_lane in ("adjacent-ai-dev", "token-efficiency-cli"):
    test(
        f"existing lane '{_existing_lane}' still present after adding claude-code-skills",
        _existing_lane in _ccs_all_lanes,
        str(_ccs_all_lanes),
    )

# I. claude-code-skills lane negative: generic "token" repo without claude topics NOT favored
_GENERIC_TOKEN_REPO: dict = {
    "full_name": "example/generic-token-counter",
    "name": "generic-token-counter",
    "description": "Count tokens in text files",
    "html_url": "https://github.com/example/generic-token-counter",
    "created_at": "2023-06-01T00:00:00Z",
    "pushed_at": "2024-01-01T12:00:00Z",
    "stargazers_count": 3,
    "forks_count": 0,
    "watchers_count": 3,
    "open_issues_count": 0,
    "language": "Python",
    "topics": ["tokenizer", "nlp"],
    "fork": False,
    "archived": False,
    "license": None,
}
_generic_score = ts.score_repo(_GENERIC_TOKEN_REPO, _ccs_score_cfg)
_caveman_score2 = ts.score_repo(CAVEMAN_LIKE_REPO, _ccs_score_cfg)
test(
    "caveman-class repo scores higher than generic token-counter repo (negative test)",
    _caveman_score2 > _generic_score,
    f"caveman={_caveman_score2:.3f}, generic={_generic_score:.3f}",
)

# J. Jcode and RTK regressions still pass with three lanes active (no displacement)
with (
    mock.patch.object(ts.GitHubClient, "search_repos", return_value=[]) as _mock_kw_3lane,
    mock.patch.object(
        ts.GitHubClient,
        "search_repos_by_topic",
        side_effect=lambda t, **kw: [JCODE_LIKE_REPO] if t in ("coding-agent", "mcp") else [],
    ) as _mock_t_3lane,
    mock.patch("time.sleep", return_value=None),
):
    _three_lane_cfg = ts.load_config(None)  # disk config now has 3 lanes
    _three_lane_cfg["search"]["seed_keywords"] = []
    _three_lane_cfg["search"]["extra_topics"] = []
    _raw_3lane = ts.search_stage(ts.GitHubClient(token="ghp_test"), _three_lane_cfg)
    test(
        "3-lane regression: jcode still found with claude-code-skills lane added",
        any(r.get("full_name") == "example/jcode-like-agent" for r in _raw_3lane),
        str([r.get("full_name") for r in _raw_3lane]),
    )


# ─── Cleanup ──────────────────────────────────────────────────────────────────

import shutil

shutil.rmtree(SCRATCH, ignore_errors=True)

# ─── Summary ──────────────────────────────────────────────────────────────────

print(f"\n{'─' * 50}")
print(f"  Results: {PASS} passed, {FAIL} failed")
print(f"{'─' * 50}\n")
sys.exit(0 if FAIL == 0 else 1)
