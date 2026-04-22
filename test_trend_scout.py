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
  - CLI: --dry-run, --search-only, --repo, --limit (subprocess)
  - JSON config file roundtrip

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
        capture_output=True, text=True, env=env, encoding="utf-8", errors="replace",
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
override_cfg_path.write_text(json.dumps({
    "target_repo": "myorg/myrepo",
    "shortlist": {"max_candidates": 99},
}))
cfg2 = ts.load_config(override_cfg_path)
test("override: target_repo changed", cfg2["target_repo"] == "myorg/myrepo")
test("override: shortlist.max_candidates overridden", cfg2["shortlist"]["max_candidates"] == 99)
test("override: nested search still has defaults", len(cfg2["search"]["seed_keywords"]) > 0)

# Explicit nulls should preserve defaults
null_cfg_path = SCRATCH / "null_override_config.json"
null_cfg_path.write_text(json.dumps({
    "analysis": {
        "model": None,
        "endpoint": None,
        "timeout": None,
        "token_env": None,
    }
}))
cfg_null = ts.load_config(null_cfg_path)
test("null override keeps default analysis model", cfg_null["analysis"]["model"] == ts.DEFAULT_MODELS_MODEL)
test("null override keeps default analysis endpoint", cfg_null["analysis"]["endpoint"] == ts.MODELS_API_ENDPOINT)
test("null override keeps default analysis timeout", cfg_null["analysis"]["timeout"] == 30)
test("null override keeps default analysis token env", cfg_null["analysis"]["token_env"] == "GITHUB_MODELS_TOKEN")

# Malformed JSON falls back to defaults gracefully
bad_cfg_path = SCRATCH / "bad_config.json"
bad_cfg_path.write_text("{not valid json}")
cfg3 = ts.load_config(bad_cfg_path)
test("malformed config falls back to defaults", cfg3["target_repo"] == "magicpro97/copilot-session-knowledge")

# Config file on disk is valid JSON
test("trend-scout-config.json exists", CONFIG_FILE.exists())
if CONFIG_FILE.exists():
    try:
        disk_cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        test("trend-scout-config.json is valid JSON", True)
        test("disk config has target_repo", "target_repo" in disk_cfg)
        test("disk config analysis endpoint uses models.github.ai",
             disk_cfg.get("analysis", {}).get("endpoint", "").startswith("https://models.github.ai/"))
        test("disk config analysis model is publisher-qualified",
             "/" in disk_cfg.get("analysis", {}).get("model", ""))
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
test("marker hash is 16 hex chars", bool(
    __import__("re").search(r"trend-scout:repo:[a-f0-9]{16}", m1)
))

# extract_markers_from_body
body_with_markers = (
    "Some text\n"
    "<!-- trend-scout:repo:abcdef1234567890 -->\n"
    "More text\n"
    "<!-- trend-scout:repo:0000111122223333 -->\n"
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
test("relevant repo scores higher than unrelated", score_good > score_bad,
     f"good={score_good}, bad={score_bad}")
test("score is float", isinstance(score_good, float))
test("score is non-negative", score_good >= 0)

# Shortlisting
candidates = [REPO_FIXTURE, unrelated, REPO_FORK, REPO_ARCHIVED, REPO_INACTIVE]
sl_cfg = {**default_cfg, "shortlist": {**default_cfg["shortlist"], "max_candidates": 3, "min_score": 0.0, "exclude_forks": True}}
shortlisted = ts.shortlist_repos(candidates, sl_cfg)
test("shortlist caps at max_candidates", len(shortlisted) <= 3)
test("shortlist excludes forks (exclude_forks=True)", all(not r.get("fork") for r in shortlisted))
test("shortlist deduplicates by full_name", len({r["full_name"] for r in shortlisted}) == len(shortlisted))
test("shortlist sorted descending by score", (
    lambda sl: all(
        ts.score_repo(sl[i], sl_cfg) >= ts.score_repo(sl[i+1], sl_cfg)
        for i in range(len(sl)-1)
    )
)(shortlisted))

# min_score filter
high_min_cfg = {**default_cfg, "shortlist": {**default_cfg["shortlist"], "min_score": 9999.0}}
shortlisted_none = ts.shortlist_repos(candidates, high_min_cfg)
test("min_score=9999 yields empty shortlist", len(shortlisted_none) == 0)


# ─── 4. Heuristic Derivation ──────────────────────────────────────────────────

print("\n🧠 Heuristic Derivation")

# _derive_problem
problem_with_desc = ts._derive_problem(REPO_FIXTURE, "readme text")
test("_derive_problem uses description when available", problem_with_desc == REPO_FIXTURE["description"])

no_desc_repo: dict = {**REPO_FIXTURE, "description": ""}
problem_from_readme = ts._derive_problem(no_desc_repo, "This is a long enough readme line to be useful here.")
test("_derive_problem falls back to readme when no description",
     "readme" in problem_from_readme.lower())

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
test("_derive_weaknesses flags inactive repos", any("inactive" in r.lower() or "push" in r.lower() for r in inactive_risks))

# _derive_learnings
our_topics = ["ai-tools", "copilot", "fts5", "knowledge-base", "python", "sqlite"]
learnings = ts._derive_learnings(REPO_FIXTURE, our_topics)
test("_derive_learnings returns non-empty list", len(learnings) > 0)

novel_topic_repo: dict = {**REPO_FIXTURE, "topics": ["novel-topic-xyz", "ai-tools"]}
novel_learnings = ts._derive_learnings(novel_topic_repo, our_topics)
# Novel-only repos now fall back to a concrete architectural review bullet instead of
# emitting a generic "Novel topic signals" dump.
test("_derive_learnings: novel-only repo uses concrete fallback (not generic topic list)",
     any("fts5" in l.lower() or "knowledge" in l.lower() or "session" in l.lower()
         for l in novel_learnings),
     str(novel_learnings))

# Regression: no bare "Novel topics to explore: ..." or "Novel topic signals" dumps
test("_derive_learnings no longer emits bare topic-list bullets",
     not any(l.startswith("Novel topics to explore:") for l in novel_learnings))
test("_derive_learnings no longer emits 'Novel topic signals' bullets",
     not any("novel topic signals" in l.lower() for l in novel_learnings),
     str(novel_learnings))

# AI-IQ-like fixture: hybrid search + graph intelligence + zero config in description
AI_IQ_LIKE_REPO: dict = {
    **REPO_FIXTURE,
    "full_name": "kobie3717/ai-iq",
    "description": (
        "AI-IQ: Persistent context system for AI coding assistants. "
        "Hybrid search (FTS+semantic), graph intelligence, zero config."
    ),
    "topics": [
        "ai", "ai-agents", "ai-tools", "claude-code", "cursor",
        "developer-tools", "fts5", "knowledge-graph", "llm", "memory", "sqlite",
    ],
}
AI_IQ_README = (
    "Give your AI long-term memory in 1 command.\n"
    "memory-tool add learning \"Docker needs network_mode: host\"\n"
    "memory-tool search \"docker networking\"\n"
    "memory-tool dream  # Consolidate duplicates, detect conflicts\n"
    "Hybrid search (FTS+semantic) for context recall.\n"
)

ai_iq_learnings = ts._derive_learnings(AI_IQ_LIKE_REPO, our_topics, AI_IQ_README)

test("_derive_learnings emits hybrid-search bullet for hybrid-search repo",
     any("hybrid" in l.lower() or "semantic" in l.lower() for l in ai_iq_learnings),
     str(ai_iq_learnings))

test("_derive_learnings emits graph bullet for knowledge-graph repo",
     any("graph" in l.lower() for l in ai_iq_learnings),
     str(ai_iq_learnings))

test("_derive_learnings emits consolidation bullet from readme signal",
     any("consolidat" in l.lower() or "dream" in l.lower() for l in ai_iq_learnings),
     str(ai_iq_learnings))

test("_derive_learnings emits CLI bullet from readme memory-tool signal",
     any("cli" in l.lower() or "verb" in l.lower() or "memory-tool" in l.lower()
         for l in ai_iq_learnings),
     str(ai_iq_learnings))

test("_derive_learnings emits Claude Code bullet for claude-code topic",
     any("claude" in l.lower() for l in ai_iq_learnings),
     str(ai_iq_learnings))

test("_derive_learnings bullets are narrative (contain 'could', 'e.g.', 'would', or 'suggest')",
     all(
         "could" in l.lower() or "e.g." in l.lower() or "would" in l.lower() or "suggest" in l.lower()
         for l in ai_iq_learnings
     ),
     str(ai_iq_learnings))

test("_derive_learnings: no bare 'Novel topics to explore:' bullet for ai-iq-like repo",
     not any(l.startswith("Novel topics to explore:") for l in ai_iq_learnings))

# Readme-only signals: description is minimal, hints come solely from readme_excerpt
readme_only_repo: dict = {**REPO_FIXTURE, "description": "A tool", "topics": []}
readme_learnings = ts._derive_learnings(
    readme_only_repo, our_topics,
    "This project uses hybrid FTS + semantic search to consolidate memories and detect conflicts."
)
test("_derive_learnings fires on readme_excerpt signals (not just description)",
     any("hybrid" in l.lower() or "semantic" in l.lower() or "consolidat" in l.lower()
         for l in readme_learnings),
     str(readme_learnings))

# Fallback is concrete when no signals fire
no_signal_repo: dict = {**REPO_FIXTURE, "description": "A simple utility", "topics": []}
fallback_learnings = ts._derive_learnings(no_signal_repo, our_topics, "")
test("_derive_learnings fallback is concrete (mentions FTS5 or knowledge-base)",
     any("fts5" in l.lower() or "knowledge" in l.lower() or "session" in l.lower()
         for l in fallback_learnings),
     str(fallback_learnings))

# GitHub Models helpers
sanitized_bullet = ts._sanitize_learning_bullet("  **Pattern**: line 1\n\nline 2 <b>tag</b>  ")
test("_sanitize_learning_bullet collapses embedded newlines",
     sanitized_bullet is not None and "\n" not in sanitized_bullet,
     str(sanitized_bullet))
test("_sanitize_learning_bullet strips raw HTML tags",
     sanitized_bullet is not None and "<b>" not in sanitized_bullet and "</b>" not in sanitized_bullet,
     str(sanitized_bullet))

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
test("_analyze_repo_with_models returns None on null content",
     null_content_learnings is None)

# Regression: substring false positives for CLI and sync detectors
cli_fp_repo: dict = {**REPO_FIXTURE, "description": "A HTTP client using click for async operations", "topics": []}
cli_fp_learnings = ts._derive_learnings(cli_fp_repo, our_topics, "client click async processing")
test("_derive_learnings: 'client' does NOT trigger CLI bullet",
     not any("cli verb" in l.lower() or "add/search/update" in l.lower() for l in cli_fp_learnings),
     str(cli_fp_learnings))
test("_derive_learnings: 'click' does NOT trigger CLI bullet",
     not any("cli verb" in l.lower() or "add/search/update" in l.lower() for l in cli_fp_learnings),
     str(cli_fp_learnings))
test("_derive_learnings: 'async' does NOT trigger cross-env sync bullet",
     not any("windows" in l.lower() or "wsl" in l.lower() or "cross-environment sync" in l.lower()
             for l in cli_fp_learnings),
     str(cli_fp_learnings))

# Positive: exact word "cli" still fires correctly
cli_exact_repo: dict = {**REPO_FIXTURE, "description": "A cli tool with sync support for knowledge", "topics": []}
cli_exact_learnings = ts._derive_learnings(cli_exact_repo, our_topics, "")
test("_derive_learnings: bare 'cli' word still triggers CLI bullet",
     any("cli verb" in l.lower() or "add/search/update" in l.lower() for l in cli_exact_learnings),
     str(cli_exact_learnings))
# Bare "sync" alone is NOT sufficient — cross-platform signal is required.
test("_derive_learnings: bare 'sync' alone does NOT trigger cross-env sync bullet",
     not any("cross-environment sync" in l.lower() or "sync-knowledge" in l.lower()
             for l in cli_exact_learnings),
     str(cli_exact_learnings))
# Positive: explicit cross-platform signal does fire the sync bullet
wsl_sync_repo: dict = {**REPO_FIXTURE, "description": "A knowledge tool with wsl cross-platform support", "topics": []}
wsl_sync_learnings = ts._derive_learnings(wsl_sync_repo, our_topics, "")
test("_derive_learnings: 'wsl' keyword triggers cross-env sync bullet",
     any("cross-environment sync" in l.lower() or "sync-knowledge" in l.lower()
         for l in wsl_sync_learnings),
     str(wsl_sync_learnings))

# Cap: output never exceeds MAX_HEURISTIC_LEARNINGS even for high-signal repos
test("_derive_learnings: result capped at MAX_HEURISTIC_LEARNINGS",
     len(ai_iq_learnings) <= ts._MAX_HEURISTIC_LEARNINGS,
     f"got {len(ai_iq_learnings)} bullets")

# Editor integration: only fires on topic match, NOT bare keyword in description/readme
editor_kw_repo: dict = {**REPO_FIXTURE, "description": "Move cursor position in vscode extension", "topics": []}
editor_kw_learnings = ts._derive_learnings(editor_kw_repo, our_topics, "check cursor and vscode settings")
test("_derive_learnings: editor keywords alone do NOT trigger editor bullet",
     not any("editor integration" in l.lower() for l in editor_kw_learnings),
     str(editor_kw_learnings))

editor_topic_repo: dict = {**REPO_FIXTURE, "description": "A memory tool", "topics": ["cursor", "ai-tools"]}
editor_topic_learnings = ts._derive_learnings(editor_topic_repo, our_topics, "")
test("_derive_learnings: 'cursor' in topics DOES trigger editor bullet",
     any("editor integration" in l.lower() for l in editor_topic_learnings),
     str(editor_topic_learnings))

# Claude Code prioritised: fires as first bullet when topic matches
claude_code_repo: dict = {**REPO_FIXTURE, "description": "Hybrid search semantic knowledge", "topics": ["claude-code", "knowledge-graph"]}
claude_code_learnings = ts._derive_learnings(claude_code_repo, our_topics, "")
test("_derive_learnings: claude-code topic produces a bullet",
     any("claude" in l.lower() for l in claude_code_learnings),
     str(claude_code_learnings))
test("_derive_learnings: claude-code bullet is first (highest priority)",
     claude_code_learnings and "claude" in claude_code_learnings[0].lower(),
     str(claude_code_learnings))


# ─── Issue #3 Regression Tests ────────────────────────────────────────────────

print("\n🐛 Issue #3 — Heuristic Quality Regressions")

# --- Portability false positive: Python 'from x import y' must NOT fire portability bullet ---
# Confirmed false positive from issue3-output-audit: portability heuristic matched 'import'
# substring in 'from ai_iq import Memory' code block, producing a spurious portability bullet.
# Fix: remove 'import' from portability keyword list; keep only 'export', 'portable', 'backup'.
portability_fp_repo: dict = {**REPO_FIXTURE, "description": "A Python library", "topics": []}
portability_fp_readme = (
    "## Quick Start\n\n"
    "```python\n"
    "from ai_iq import Memory\n"
    "m = Memory()\n"
    "m.add('learning')\n"
    "```\n"
)
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
    our_topics, "",
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
            "offline" in l.lower() or "no-cloud" in l.lower() or "no cloud" in l.lower()
            or "cloud" in l.lower()
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
    any(
        "offline" in l.lower() or "no-cloud" in l.lower() or "cloud" in l.lower()
        for l in _boundary_learnings
    ),
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
    _reflect_base_repo, our_topics,
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
    our_topics, "",
)
test(
    "issue#3 reflexion FP: bare 'reflect' in Java library description does NOT trigger structured reflexion bullet",
    not _fires_reflexion(_java_reflect_word_learnings),
    str(_java_reflect_word_learnings),
)

# Negative regression — general programming 'reflect' context (readme only)
_general_reflect_learnings = ts._derive_learnings(
    _reflect_base_repo, our_topics,
    "Provides reflect utilities to inspect Python objects at runtime",
)
test(
    "issue#3 reflexion FP: generic 'reflect' introspection context does NOT trigger structured reflexion bullet",
    not _fires_reflexion(_general_reflect_learnings),
    str(_general_reflect_learnings),
)

# Positive control — genuine AI/agent reflexion signal: 'reflexion' keyword
_genuine_reflexion_learnings = ts._derive_learnings(
    _reflect_base_repo, our_topics,
    "Implements an agent reflexion loop: pre-task memory recall and post-task structured debriefing",
)
test(
    "issue#3 reflexion positive: 'reflexion' keyword in AI-agent readme DOES trigger structured reflexion bullet",
    _fires_reflexion(_genuine_reflexion_learnings),
    str(_genuine_reflexion_learnings),
)

# Positive control — genuine signal: 'structured reflexion' phrase
_structured_reflexion_learnings = ts._derive_learnings(
    _reflect_base_repo, our_topics,
    "Structured reflexion with worked/failed/next fields for outcome-aware learning",
)
test(
    "issue#3 reflexion positive: 'structured reflexion' phrase DOES trigger structured reflexion bullet",
    _fires_reflexion(_structured_reflexion_learnings),
    str(_structured_reflexion_learnings),
)

# Positive control — genuine signal: 'post-mortem' (no false-positive risk)
_post_mortem_learnings = ts._derive_learnings(
    _reflect_base_repo, our_topics,
    "Captures post-mortem analysis after each task to feed future briefings",
)
test(
    "issue#3 reflexion positive: 'post-mortem' keyword DOES trigger structured reflexion bullet",
    _fires_reflexion(_post_mortem_learnings),
    str(_post_mortem_learnings),
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
body2 = ts.render_issue_body({**REPO_FIXTURE, "full_name": "otheruser/different-repo",
                               "html_url": "https://github.com/otheruser/different-repo"},
                              "", marker2, our_topics)
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

# 404 returns None gracefully
import urllib.error as _ue
with mock.patch("urllib.request.urlopen") as mock_open:
    err = _ue.HTTPError(url="https://api.github.com/x", code=404, msg="Not Found",
                        hdrs=mock.MagicMock(get=lambda k, d=None: d), fp=None)
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

with mock.patch.object(ts.GitHubClient, "ensure_label", return_value=True), \
     mock.patch.object(ts.GitHubClient, "create_issue", return_value=None):
    client = ts.GitHubClient(token="ghp_test")
    cfg_c = ts.load_config(None)
    enriched = [(REPO_FIXTURE, "readme text"), (REPO_INACTIVE, "")]
    existing: set[str] = set()
    urls = ts.create_stage(enriched, client, cfg_c, existing, dry_run=True, limit=None)
    test("dry-run returns URLs for both repos", len(urls) == 2)
    test("dry-run URLs contain [dry-run] tag", all("[dry-run]" in u for u in urls))
    test("dry-run adds markers to existing set (prevents duplicates)", len(existing) == 2)

# Limit parameter
with mock.patch.object(ts.GitHubClient, "ensure_label", return_value=True), \
     mock.patch.object(ts.GitHubClient, "create_issue", return_value=None):
    enriched2 = [(REPO_FIXTURE, ""), (REPO_INACTIVE, ""), (unrelated, "")]
    existing2: set[str] = set()
    urls2 = ts.create_stage(enriched2, client, cfg_c, existing2, dry_run=True, limit=1)
    test("limit=1 creates only 1 issue", len(urls2) == 1)

# Dedup: already-seen marker skips
with mock.patch.object(ts.GitHubClient, "ensure_label", return_value=True), \
     mock.patch.object(ts.GitHubClient, "create_issue", return_value=None):
    pre_existing = {ts.repo_marker(REPO_FIXTURE["full_name"])}
    urls3 = ts.create_stage([(REPO_FIXTURE, "")], client, cfg_c, pre_existing, dry_run=True)
    test("already-seen marker is skipped", len(urls3) == 0)


# ─── 8b. create_stage update path (mocked) ────────────────────────────────────

print("\n🔄 create_stage update path (mocked)")

_marker_update = ts.repo_marker(REPO_FIXTURE["full_name"])
_old_body = "old body content that differs from newly rendered body"
_issue_map_open: dict = {
    _marker_update: {"number": 42, "state": "open", "body": _old_body, "title": "old title"}
}
_issue_map_closed: dict = {
    _marker_update: {"number": 99, "state": "closed", "body": _old_body, "title": "old title"}
}

# dry-run with issue_map: should show [would-update] for existing issue
with mock.patch.object(ts.GitHubClient, "patch_issue", return_value=None) as mock_patch:
    client_u = ts.GitHubClient(token="ghp_test")
    existing_u: set[str] = {_marker_update}  # marker is in existing_markers too
    urls_update_dry = ts.create_stage(
        [(REPO_FIXTURE, "readme text")],
        client_u, cfg_c, existing_u,
        dry_run=True,
        issue_map=_issue_map_open,
    )
    test("dry-run update: returns a URL for the existing issue number",
         len(urls_update_dry) == 1 and "42" in urls_update_dry[0],
         str(urls_update_dry))
    test("dry-run update: URL contains [dry-run] tag",
         urls_update_dry and "[dry-run]" in urls_update_dry[0])
    test("dry-run update: patch_issue NOT called in dry-run mode",
         mock_patch.call_count == 0)

# live update: patch_issue called, body differs
with mock.patch.object(ts.GitHubClient, "patch_issue",
                       return_value={"html_url": "https://github.com/x/y/issues/42"}) as mock_patch2:
    client_u2 = ts.GitHubClient(token="ghp_test")
    existing_u2: set[str] = {_marker_update}
    urls_live_update = ts.create_stage(
        [(REPO_FIXTURE, "readme text")],
        client_u2, cfg_c, existing_u2,
        dry_run=False,
        issue_map=_issue_map_open,
    )
    test("live update: patch_issue called once",
         mock_patch2.call_count == 1,
         f"call_count={mock_patch2.call_count}")
    test("live update: returns URL from patch response",
         urls_live_update == ["https://github.com/x/y/issues/42"],
         str(urls_live_update))
    # Verify patch_issue was NOT called with a state change
    patch_kwargs = mock_patch2.call_args.kwargs if mock_patch2.call_args else {}
    test("live update: patch_issue has no state param (does not reopen closed issues)",
         "state" not in patch_kwargs)

# unchanged body: should be skipped even when in issue_map
with mock.patch.object(ts.GitHubClient, "patch_issue", return_value=None) as mock_patch3:
    client_u3 = ts.GitHubClient(token="ghp_test")
    # Use the actual rendered body so the comparison matches
    _real_marker = ts.repo_marker(REPO_FIXTURE["full_name"])
    _real_body = ts.render_issue_body(REPO_FIXTURE, "readme text", _real_marker,
                                      cfg_c.get("search", {}).get("our_topics", []))
    _issue_map_same = {_real_marker: {"number": 77, "state": "open", "body": _real_body}}
    urls_unchanged = ts.create_stage(
        [(REPO_FIXTURE, "readme text")],
        client_u3, cfg_c, {_real_marker},
        dry_run=False,
        issue_map=_issue_map_same,
    )
    test("unchanged body: patch_issue NOT called",
         mock_patch3.call_count == 0,
         f"call_count={mock_patch3.call_count}")
    test("unchanged body: no URL emitted",
         len(urls_unchanged) == 0,
         str(urls_unchanged))

# closed issue: patch updates body but does NOT reopen (state not passed to patch_issue)
with mock.patch.object(ts.GitHubClient, "patch_issue",
                       return_value={"html_url": "https://github.com/x/y/issues/99"}) as mock_patch4:
    client_u4 = ts.GitHubClient(token="ghp_test")
    urls_closed_update = ts.create_stage(
        [(REPO_FIXTURE, "readme text")],
        client_u4, cfg_c, {_marker_update},
        dry_run=False,
        issue_map=_issue_map_closed,
    )
    test("closed issue update: patch_issue called (body refreshed)",
         mock_patch4.call_count == 1)
    closed_kwargs = mock_patch4.call_args.kwargs if mock_patch4.call_args else {}
    test("closed issue update: state NOT in patch call (issue stays closed)",
         "state" not in closed_kwargs)


# ─── 8c. get_existing_issue_map (mocked) ──────────────────────────────────────

print("\n🗂  get_existing_issue_map (mocked)")

_map_marker_a = ts.repo_marker("owner/repo-a")
_map_marker_b = ts.repo_marker("owner/repo-b")
_map_issues: list = [
    {"number": 10, "state": "open",   "title": "T-A", "body": f"body\n{_map_marker_a}",     "pull_request": None},
    {"number": 20, "state": "closed", "title": "T-B", "body": f"body\n{_map_marker_b}",     "pull_request": None},
    {"number": 30, "state": "open",   "title": "T-PR", "body": f"{_map_marker_a}",          "pull_request": {"url": "x"}},  # skip PR
]

def _mock_list_issues_map(repo, state="all", per_page=100, page=1, labels=None):
    if page == 1:
        return _map_issues
    return []

with mock.patch.object(ts.GitHubClient, "list_issues", side_effect=_mock_list_issues_map):
    client_map = ts.GitHubClient(token="ghp_test")
    imap = ts.get_existing_issue_map(client_map, "owner/repo", ts.load_config(None))
    test("get_existing_issue_map: marker_a present", _map_marker_a in imap,
         f"keys={list(imap.keys())}")
    test("get_existing_issue_map: marker_b present", _map_marker_b in imap)
    test("get_existing_issue_map: PR entry skipped (only 2 entries)",
         len(imap) == 2, f"len={len(imap)}")
    test("get_existing_issue_map: issue number preserved",
         imap[_map_marker_a]["number"] == 10,
         str(imap.get(_map_marker_a)))
    test("get_existing_issue_map: closed state preserved",
         imap[_map_marker_b]["state"] == "closed")
    # get_existing_markers delegates to issue_map
    with mock.patch.object(ts.GitHubClient, "list_issues", side_effect=_mock_list_issues_map):
        markers_set = ts.get_existing_markers(client_map, "owner/repo", ts.load_config(None))
    test("get_existing_markers still works after refactor",
         _map_marker_a in markers_set and _map_marker_b in markers_set)


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
    test("patch_issue: returns parsed dict on success",
         isinstance(patch_result, dict) and patch_result.get("number") == 5,
         str(patch_result))
    test("patch_issue: html_url in response",
         patch_result is not None and "html_url" in patch_result)

# patch_issue returns None on HTTP error
def _mock_patch_404(*args, **kwargs):
    raise urllib.error.HTTPError("https://api.github.com/repos/x/y/issues/999",
                                  404, "Not Found", mock.MagicMock(), None)

with mock.patch("urllib.request.urlopen", side_effect=_mock_patch_404):
    client_patch2 = ts.GitHubClient(token="ghp_test")
    patch_fail = client_patch2.patch_issue("x/y", 999, "Title", "Body")
    test("patch_issue: returns None on HTTP 404",
         patch_fail is None)



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

with mock.patch("urllib.request.urlopen", side_effect=_always_403), \
     mock.patch("time.sleep"):  # don't actually sleep in tests
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
with mock.patch.object(ts, "search_stage", return_value=[]), \
     mock.patch.object(ts, "shortlist_repos", return_value=[]), \
     mock.patch.object(ts, "ModelsClient") as mock_models, \
     mock.patch.dict(os.environ, {"GITHUB_TOKEN": "ghs_actions_token"}, clear=True):
    explicit_token_cfg = ts.load_config(None)
    explicit_token_cfg["analysis"]["enabled"] = True
    explicit_token_cfg["analysis"]["token_env"] = "GITHUB_MODELS_TOKEN"
    ts.run(explicit_token_cfg, dry_run=True, search_only=True)
    test(
        "models auth: missing configured token_env does not silently fall back to GITHUB_TOKEN",
        mock_models.call_count == 0,
        f"ModelsClient call count={mock_models.call_count}",
    )

with mock.patch.object(ts, "search_stage", return_value=[]), \
     mock.patch.object(ts, "shortlist_repos", return_value=[]), \
     mock.patch.object(ts, "ModelsClient") as mock_models, \
     mock.patch.dict(os.environ, {"GITHUB_TOKEN": "ghs_actions_token"}, clear=True):
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

with mock.patch.object(ts, "search_stage", return_value=[]), \
     mock.patch.object(ts, "shortlist_repos", return_value=[]), \
     mock.patch.object(ts, "ModelsClient") as mock_models, \
     mock.patch.dict(os.environ, {"GITHUB_MODELS_TOKEN": "ghm_pat"}, clear=True):
    invalid_model_cfg = ts.load_config(None)
    invalid_model_cfg["analysis"]["enabled"] = True
    invalid_model_cfg["analysis"]["model"] = "gpt-4o-mini"
    ts.run(invalid_model_cfg, dry_run=True, search_only=True)
    test(
        "models config: invalid unqualified model id skips ModelsClient construction",
        mock_models.call_count == 0,
        f"ModelsClient call count={mock_models.call_count}",
    )

with mock.patch.object(ts, "search_stage", return_value=[]), \
     mock.patch.object(ts, "shortlist_repos", return_value=[]), \
     mock.patch.object(ts, "ModelsClient") as mock_models, \
     mock.patch.dict(os.environ, {"GITHUB_MODELS_TOKEN": "ghm_pat"}, clear=True):
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
        ts.load_config(None),
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
multi_fence_body = (
    f"```\n{fake_marker}\n```\nsome text\n"
    f"```python\n# code\n{fake_marker}\n```\n"
    f"{real_marker}\n"
)
multi_extracted = ts.extract_markers_from_body(multi_fence_body, prefix)
test(
    "marker spoofing: markers in multiple code fences all ignored",
    fake_marker not in multi_extracted and real_marker in multi_extracted,
    f"extracted={multi_extracted}",
)


# ─── Cleanup ──────────────────────────────────────────────────────────────────

import shutil
shutil.rmtree(SCRATCH, ignore_errors=True)

# ─── Summary ──────────────────────────────────────────────────────────────────

print(f"\n{'─' * 50}")
print(f"  Results: {PASS} passed, {FAIL} failed")
print(f"{'─' * 50}\n")
sys.exit(0 if FAIL == 0 else 1)
