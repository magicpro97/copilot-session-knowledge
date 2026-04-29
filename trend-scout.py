#!/usr/bin/env python3
"""
trend-scout.py — GitHub-native daily trend scouting for magicpro97/copilot-session-knowledge.

Discovers trending/relevant repos via GitHub Search API (no scraping), scores them,
enriches with metadata, and creates structured issues in the target repo.

Stages:
  1. Search  — rough discovery via GitHub Search API (keyword + topic queries)
  2. Shortlist — score and deduplicate candidates
  3. Enrich  — fetch README and metadata for shortlisted repos
  4. Render  — build structured issue body with hidden deterministic marker
  5. Create  — ensure label exists, deduplicate against existing issues, create

Usage:
    python3 trend-scout.py                      # Full pipeline
    python3 trend-scout.py --dry-run            # Preview without creating issues
    python3 trend-scout.py --repo owner/repo    # Override target repo
    python3 trend-scout.py --config path.json   # Use custom config
    python3 trend-scout.py --search-only        # Discovery + shortlist only, no issues
    python3 trend-scout.py --limit N            # Cap number of issues created
    python3 trend-scout.py --token TOKEN        # Explicit GitHub token

Environment:
    GITHUB_TOKEN — GitHub personal access token (recommended to avoid rate limits)
"""

import argparse
import base64
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Fix Windows console encoding
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "trend-scout-config.json"
DEFAULT_GOLDSET_PATH = SCRIPT_DIR / "trend-scout-goldset.json"

GITHUB_API = "https://api.github.com"
MODELS_API_ENDPOINT = "https://models.github.ai/inference/chat/completions"
MODELS_API_VERSION = "2022-11-28"
DEFAULT_MODELS_MODEL = "openai/gpt-4o-mini"
REQUEST_TIMEOUT = 30
RATE_LIMIT_SLEEP_CAP = 120  # never sleep more than 2 min at once

# Input safety limits
MAX_QUERY_LEN = 256
MAX_LABEL_LEN = 50
MAX_TITLE_LEN = 256

DEFAULT_CONFIG: dict = {
    "target_repo": "magicpro97/copilot-session-knowledge",
    "issue_label": "trend-scout",
    "issue_label_color": "0075ca",
    "issue_label_description": "Auto-generated trend scouting report",
    "issue_title_prefix": "[Trend Scout]",
    "search": {
        "seed_keywords": [
            "AI coding session knowledge base sqlite",
            "copilot session indexing fts5 python",
            "LLM context memory sqlite search",
            "AI assistant knowledge management python",
        ],
        "extra_topics": ["ai-tools", "knowledge-base", "semantic-search", "copilot"],
        "our_topics": [
            "ai-tools", "copilot", "fts5", "github-copilot",
            "knowledge-base", "python", "semantic-search", "sqlite",
        ],
        "min_stars": 5,
        "max_per_query": 10,
        "lookback_days": 730,
        "language": "python",
    },
    "shortlist": {
        "max_candidates": 5,
        "min_score": 0.15,
        "exclude_forks": True,
        "exclude_archived": False,
        "scoring": {
            "keyword_match_weight": 2.0,
            "topic_match_weight": 1.5,
            "star_log_weight": 0.3,
            "recency_weight": 0.8,
        },
    },
    "enrichment": {
        "fetch_readme": True,
        "readme_max_chars": 3000,
        "fetch_root_contents": False,
    },
    "dedup": {
        "marker_prefix": "trend-scout:repo:",
        "search_closed_issues": True,
        "max_issues_scan": 300,
    },
    "analysis": {
        # Set enabled=true and export GITHUB_MODELS_TOKEN to activate the LLM path.
        # Falls back to heuristic _derive_learnings() on any failure.
        "enabled": False,
        "model": DEFAULT_MODELS_MODEL,
        "endpoint": MODELS_API_ENDPOINT,
        "temperature": 0.2,
        "max_tokens": 800,
        "max_learnings": 5,
        "timeout": 30,
        # Which env var holds the models-capable token.
        # Use GITHUB_MODELS_TOKEN locally, or set this to GITHUB_TOKEN in Actions
        # when the workflow grants permissions: models: read.
        "token_env": "GITHUB_MODELS_TOKEN",
    },
    "veto": {
        # Rowboat veto gate: set require_domain_signals>=1 to skip repos whose
        # heuristic learning engine produces only the generic fallback bullet.
        # min_distinct_learnings>=2 additionally requires multiple distinct
        # insights so single generic-ish bullets do not create an issue.
        # 0 = disabled (all shortlisted repos are written).
        "require_domain_signals": 0,
        "min_distinct_learnings": 0,
    },
    "run_control": {
        # Grace window prevents repeated runs too close together.
        # 0 = disabled; the config file sets this for scheduled runs.
        # state_file=None resolves to .trend-scout-state.json adjacent to this script.
        "grace_window_hours": 0,
        "state_file": None,
    },
    # Additional discovery lanes beyond the primary search lane.
    # Each lane has the same shape as the "search" section but runs independently
    # so that repos with a different language/keyword surface area are not
    # crowded out by primary-lane candidates.  All lane results pool into the
    # same shortlist stage.  Discovery source is tagged on each repo for
    # explainability.  An empty list (default) means single-lane mode.
    "lanes": [],
}


# ═══════════════════════════════════════════════════════════════════════════════
#  Config
# ═══════════════════════════════════════════════════════════════════════════════

def load_config(path: Path | None = None) -> dict:
    """Load config from JSON file, merging with defaults (file wins for non-null values)."""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    source = path or DEFAULT_CONFIG_PATH
    if source.exists():
        try:
            loaded = json.loads(source.read_text(encoding="utf-8"))
            _deep_merge(cfg, loaded)
        except Exception as e:
            print(f"  ⚠ Could not load config from {source}: {e}", file=sys.stderr)
    return cfg


def load_goldset(path: Path | None = None) -> dict:
    """Load the optional Trend Scout gold-set/watchlist file."""
    source = path or DEFAULT_GOLDSET_PATH
    out = {"path": str(source), "entries": []}
    if not source.exists():
        return out
    try:
        loaded = json.loads(source.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  ⚠ Could not load gold-set from {source}: {e}", file=sys.stderr)
        return out
    if not isinstance(loaded, dict):
        print(f"  ⚠ Gold-set file {source} must contain a JSON object", file=sys.stderr)
        return out

    entries = loaded.get("entries", [])
    if not isinstance(entries, list):
        print(f"  ⚠ Gold-set file {source} has non-list 'entries'; ignoring", file=sys.stderr)
        return out

    normalized: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        repo = str(entry.get("repo", "") or "").strip()
        if not repo or "/" not in repo:
            continue
        expected_lane = str(entry.get("expected_lane", "") or "").strip() or None
        category = str(entry.get("category", "") or "").strip() or None
        notes = str(entry.get("notes", "") or "").strip() or None
        min_score = entry.get("min_score")
        try:
            min_score = float(min_score) if min_score is not None else None
        except (TypeError, ValueError):
            min_score = None
        normalized.append({
            "repo": repo,
            "required": bool(entry.get("required", True)),
            "expected_lane": expected_lane,
            "category": category,
            "min_score": min_score,
            "notes": notes,
        })
    out["entries"] = normalized
    return out


def _deep_merge(base: dict, override: dict) -> None:
    """Merge override into base in-place; nested dicts are merged recursively."""
    for k, v in override.items():
        if k.startswith("_"):
            continue  # skip comment keys
        if v is None:
            continue  # explicit null means "use default"
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


# ═══════════════════════════════════════════════════════════════════════════════
#  GitHub API Client
# ═══════════════════════════════════════════════════════════════════════════════

class GitHubClient:
    """Minimal stdlib GitHub REST v3 client with rate-limit awareness."""

    def __init__(self, token: str | None = None):
        self.token = token or os.environ.get("GITHUB_TOKEN")
        self._remaining: int = 30
        self._reset_at: float = 0.0

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "trend-scout/1.0 (magicpro97/copilot-session-knowledge)",
        }
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _update_rate_limits(self, headers: dict) -> None:
        try:
            remaining = headers.get("X-RateLimit-Remaining") or headers.get("x-ratelimit-remaining")
            reset = headers.get("X-RateLimit-Reset") or headers.get("x-ratelimit-reset")
            if remaining is not None:
                self._remaining = int(remaining)
            if reset is not None:
                self._reset_at = float(reset)
        except (ValueError, TypeError):
            pass

    def _wait_if_rate_limited(self) -> None:
        if self._remaining <= 2:
            now = time.time()
            if self._reset_at > now:
                sleep_secs = min(self._reset_at - now + 2, RATE_LIMIT_SLEEP_CAP)
                print(
                    f"  ⏳ Rate limit low ({self._remaining} remaining) — "
                    f"sleeping {sleep_secs:.0f}s until reset…",
                    flush=True,
                )
                time.sleep(sleep_secs)
                self._remaining = 30  # optimistic reset

    def get(self, url: str, params: dict | None = None, _retries: int = 1) -> dict | list | None:
        """Make an authenticated GET request. Returns parsed JSON or None on error."""
        if params:
            url = url + "?" + urllib.parse.urlencode(
                {k: v for k, v in params.items() if v is not None}
            )
        self._wait_if_rate_limited()
        req = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                self._update_rate_limits(dict(resp.headers))
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                self._update_rate_limits(dict(e.headers))
            except Exception:
                pass
            if e.code == 403:
                retry_after = 0.0
                try:
                    retry_after = float(e.headers.get("Retry-After") or 0)
                except Exception:
                    pass
                if retry_after > 0 and _retries > 0:
                    sleep_secs = min(retry_after + 1, RATE_LIMIT_SLEEP_CAP)
                    print(f"  ⏳ Rate-limited (403) — sleeping {sleep_secs:.0f}s…", flush=True)
                    time.sleep(sleep_secs)
                    return self.get(url, _retries=_retries - 1)  # bounded single retry
                print(f"  ⚠ HTTP 403 for {url} (check GITHUB_TOKEN permissions)", file=sys.stderr)
            elif e.code == 422:
                print(f"  ⚠ HTTP 422 (unprocessable) for {url}", file=sys.stderr)
            elif e.code != 404:
                print(f"  ⚠ HTTP {e.code} for {url}: {e.reason}", file=sys.stderr)
            return None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            print(f"  ⚠ Request failed for {url}: {e}", file=sys.stderr)
            return None

    def post(self, url: str, body: dict) -> dict | None:
        """Make an authenticated POST request with JSON body."""
        self._wait_if_rate_limited()
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={**self._headers(), "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                self._update_rate_limits(dict(resp.headers))
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body_bytes = b""
            try:
                body_bytes = e.read()
            except Exception:
                pass
            print(f"  ⚠ POST HTTP {e.code} for {url}: {body_bytes[:200]}", file=sys.stderr)
            return None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            print(f"  ⚠ POST failed for {url}: {e}", file=sys.stderr)
            return None

    # ── Search ────────────────────────────────────────────────────────────────

    def search_repos(
        self,
        keywords: str,
        min_stars: int = 5,
        max_results: int = 10,
        created_after: str | None = None,
        language: str | None = None,
    ) -> list[dict]:
        """Search GitHub repos by keyword phrase. Returns list of repo dicts."""
        q_parts = [keywords[:200]]
        if min_stars > 0:
            q_parts.append(f"stars:>={min_stars}")
        if created_after:
            q_parts.append(f"created:>{created_after}")
        if language:
            q_parts.append(f"language:{language}")
        q = " ".join(q_parts)[:MAX_QUERY_LEN]

        params: dict = {
            "q": q,
            "sort": "stars",
            "order": "desc",
            "per_page": min(max(max_results, 1), 30),
        }
        result = self.get(f"{GITHUB_API}/search/repositories", params)
        if isinstance(result, dict) and "items" in result:
            return result["items"]
        return []

    def search_repos_by_topic(
        self,
        topic: str,
        min_stars: int = 5,
        max_results: int = 10,
        language: str | None = None,
    ) -> list[dict]:
        """Search GitHub repos by topic tag."""
        q_parts = [f"topic:{topic}", f"stars:>={min_stars}"]
        if language:
            q_parts.append(f"language:{language}")
        q = " ".join(q_parts)[:MAX_QUERY_LEN]
        params: dict = {
            "q": q,
            "sort": "updated",
            "order": "desc",
            "per_page": min(max(max_results, 1), 30),
        }
        result = self.get(f"{GITHUB_API}/search/repositories", params)
        if isinstance(result, dict) and "items" in result:
            return result["items"]
        return []

    # ── Enrichment ────────────────────────────────────────────────────────────

    def get_readme(self, full_name: str) -> str:
        """Fetch README content. Returns decoded text or empty string."""
        data = self.get(f"{GITHUB_API}/repos/{full_name}/readme")
        if isinstance(data, dict) and data.get("encoding") == "base64":
            try:
                raw = data["content"].replace("\n", "")
                return base64.b64decode(raw).decode("utf-8", errors="replace")
            except Exception:
                pass
        return ""

    # ── Issues ────────────────────────────────────────────────────────────────

    def list_issues(
        self,
        repo: str,
        state: str = "all",
        per_page: int = 100,
        page: int = 1,
        labels: str | None = None,
    ) -> list[dict]:
        """List issues (including PRs filtered out via pull_request key). Returns list."""
        params: dict = {"state": state, "per_page": per_page, "page": page}
        if labels:
            params["labels"] = labels
        result = self.get(f"{GITHUB_API}/repos/{repo}/issues", params)
        return result if isinstance(result, list) else []

    def create_issue(self, repo: str, title: str, body: str, labels: list[str]) -> dict | None:
        """Create a new issue. Returns created issue dict or None."""
        return self.post(
            f"{GITHUB_API}/repos/{repo}/issues",
            {"title": title[:MAX_TITLE_LEN], "body": body, "labels": labels},
        )

    def patch_issue(self, repo: str, issue_number: int, title: str, body: str) -> dict | None:
        """Update an existing issue's title and body (does NOT change state).

        Uses PATCH /repos/{repo}/issues/{number} — keeping closed issues closed.
        Returns updated issue dict or None on failure.
        """
        self._wait_if_rate_limited()
        url = f"{GITHUB_API}/repos/{repo}/issues/{issue_number}"
        data = json.dumps({"title": title[:MAX_TITLE_LEN], "body": body}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={**self._headers(), "Content-Type": "application/json"},
            method="PATCH",
        )
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                self._update_rate_limits(dict(resp.headers))
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body_bytes = b""
            try:
                body_bytes = e.read()
            except Exception:
                pass
            print(f"  ⚠ PATCH HTTP {e.code} for {url}: {body_bytes[:200]}", file=sys.stderr)
            return None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            print(f"  ⚠ PATCH failed for {url}: {e}", file=sys.stderr)
            return None

    def ensure_label(
        self,
        repo: str,
        name: str,
        color: str = "0075ca",
        description: str = "",
    ) -> bool:
        """Ensure label exists in repo; create if missing. Returns True on success."""
        existing = self.get(
            f"{GITHUB_API}/repos/{repo}/labels/{urllib.parse.quote(name, safe='')}"
        )
        if isinstance(existing, dict) and existing.get("name"):
            return True
        result = self.post(
            f"{GITHUB_API}/repos/{repo}/labels",
            {"name": name[:MAX_LABEL_LEN], "color": color, "description": description[:100]},
        )
        return result is not None


# ═══════════════════════════════════════════════════════════════════════════════
#  GitHub Models Client (OpenAI-compatible chat completions)
# ═══════════════════════════════════════════════════════════════════════════════

class ModelsClient:
    """Minimal client for GitHub Models (OpenAI-compatible) chat completions.

    Intentionally separate from GitHubClient so that inference endpoint auth and
    REST v3 rate-limit state are never mixed.  Only constructed when the analysis
    section is enabled and a models-capable token is available.
    """

    def __init__(self, token: str, endpoint: str, timeout: int = 30) -> None:
        self.token = token
        self.endpoint = endpoint
        self.timeout = timeout

    def chat_completions(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 800,
    ) -> dict | None:
        """POST to the chat completions endpoint. Returns parsed JSON or None on error."""
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint,
            data=data,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": MODELS_API_VERSION,
                "User-Agent": "trend-scout/1.0 (magicpro97/copilot-session-knowledge)",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body_bytes = b""
            try:
                body_bytes = e.read()
            except Exception:
                pass
            print(
                f"  ⚠ Models API HTTP {e.code}: {body_bytes[:200]}",
                file=sys.stderr,
            )
            return None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            print(f"  ⚠ Models API request failed: {e}", file=sys.stderr)
            return None


# ═══════════════════════════════════════════════════════════════════════════════
#  Deduplication
# ═══════════════════════════════════════════════════════════════════════════════

def repo_marker(full_name: str, marker_prefix: str = "trend-scout:repo:") -> str:
    """Compute a deterministic hidden HTML-comment marker for a repo full name."""
    h = hashlib.sha256(full_name.strip().lower().encode()).hexdigest()[:16]
    return f"<!-- {marker_prefix}{h} -->"


def extract_markers_from_body(body: str, marker_prefix: str) -> set[str]:
    """Extract all trend-scout markers from an issue body string.

    Code-fenced blocks (``` ... ```) are stripped first so that marker-like
    strings embedded in README excerpts cannot spoof the dedup set.
    """
    # Remove code-fenced blocks (including those wrapping README excerpts)
    stripped = re.sub(r"```.*?```", "", body, flags=re.DOTALL)
    pattern = r"<!--\s*" + re.escape(marker_prefix) + r"[a-f0-9]+\s*-->"
    return {m.strip() for m in re.findall(pattern, stripped)}


def get_existing_issue_map(client: GitHubClient, target_repo: str, config: dict) -> dict[str, dict]:
    """Scan open (and optionally closed) trend-scout issues for dedup markers.

    Returns a dict mapping marker string → {number, state, body, title} for every
    issue that carries a marker.  When a marker appears in multiple issues (shouldn't
    happen but defensive), the first one encountered wins.
    """
    dedup_cfg = config.get("dedup", {})
    marker_prefix: str = dedup_cfg.get("marker_prefix", "trend-scout:repo:")
    search_closed: bool = dedup_cfg.get("search_closed_issues", True)
    max_scan: int = int(dedup_cfg.get("max_issues_scan", 300))
    label: str = config.get("issue_label", "trend-scout")

    issue_map: dict[str, dict] = {}
    states = ["open", "closed"] if search_closed else ["open"]

    for state in states:
        page = 1
        fetched = 0
        while fetched < max_scan:
            issues = client.list_issues(target_repo, state=state, per_page=100, page=page, labels=label)
            if not issues:
                break
            for issue in issues:
                if issue.get("pull_request"):
                    continue
                body = issue.get("body") or ""
                markers = extract_markers_from_body(body, marker_prefix)
                for m in markers:
                    if m not in issue_map:
                        issue_map[m] = {
                            "number": issue["number"],
                            "state": issue.get("state", "open"),
                            "body": body,
                            "title": issue.get("title", ""),
                        }
            fetched += len(issues)
            if len(issues) < 100:
                break
            page += 1

    return issue_map


def get_existing_markers(client: GitHubClient, target_repo: str, config: dict) -> set[str]:
    """Return the set of dedup markers found in existing trend-scout issues.

    Delegates to get_existing_issue_map; kept for API compatibility.
    """
    return set(get_existing_issue_map(client, target_repo, config).keys())


# ═══════════════════════════════════════════════════════════════════════════════
#  Scoring / Shortlisting
# ═══════════════════════════════════════════════════════════════════════════════

def _build_term_set(seed_keywords: list[str], min_len: int = 4) -> set[str]:
    """Extract meaningful terms from seed keyword phrases."""
    terms: set[str] = set()
    stop = {"with", "from", "this", "that", "have", "will", "your", "for", "and", "the"}
    for phrase in seed_keywords:
        for word in re.split(r"\W+", phrase.lower()):
            if len(word) >= min_len and word not in stop:
                terms.add(word)
    return terms


def _build_global_term_set(config: dict) -> set[str]:
    """Build a cross-lane keyword term set for consistent scoring."""
    all_kw: list[str] = list(config.get("search", {}).get("seed_keywords", []))
    for lane in config.get("lanes", []):
        if isinstance(lane, dict):
            all_kw.extend(lane.get("keywords", []))
    return _build_term_set(all_kw)


def score_repo(repo: dict, config: dict, term_set: "set[str] | None" = None) -> float:
    """Compute a deterministic relevance score for a repo given config.

    Args:
        repo: Repository metadata dict from GitHub Search API.
        config: Loaded pipeline config.
        term_set: Pre-built keyword term set.  When provided (e.g. a cross-lane
            set built by ``shortlist_repos``), it replaces the default per-run
            build from ``config["search"]["seed_keywords"]``.  Pass this to
            ensure repos found by adjacent lanes are scored against the full
            multi-lane vocabulary instead of only primary-lane keywords.
    """
    s_cfg = config.get("shortlist", {}).get("scoring", {})
    kw_weight: float = float(s_cfg.get("keyword_match_weight", 2.0))
    topic_weight: float = float(s_cfg.get("topic_match_weight", 1.5))
    star_weight: float = float(s_cfg.get("star_log_weight", 0.3))
    recency_weight: float = float(s_cfg.get("recency_weight", 0.8))

    score = 0.0

    # Keyword matches in name, description
    search_text = " ".join(filter(None, [
        repo.get("name", ""),
        repo.get("description", ""),
        repo.get("full_name", ""),
    ])).lower()

    if term_set is None:
        seed_keywords: list[str] = config.get("search", {}).get("seed_keywords", [])
        term_set = _build_term_set(seed_keywords)
    matched = sum(1 for t in term_set if t in search_text)
    score += matched * kw_weight

    # Topic overlap — combine primary and additional lane topics so repos found
    # by an adjacent lane are not penalised for missing primary-lane topics.
    repo_topics: set[str] = set(repo.get("topics", []))
    config_topics: set[str] = set(config.get("search", {}).get("extra_topics", []))
    for _lane in config.get("lanes", []):
        config_topics.update(_lane.get("topics", []))
    score += len(repo_topics & config_topics) * topic_weight

    # Stars (log-scale to avoid viral outliers dominating)
    stars: int = repo.get("stargazers_count", 0)
    if stars > 0:
        score += math.log(stars + 1) * star_weight

    # Recency bonus
    pushed = repo.get("pushed_at") or ""
    if pushed:
        try:
            pushed_dt = datetime.fromisoformat(pushed.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - pushed_dt).days
            if age_days < 30:
                score += recency_weight
            elif age_days < 180:
                score += recency_weight * 0.7
            elif age_days < 365:
                score += recency_weight * 0.4
        except Exception:
            pass

    return round(score, 4)


def shortlist_repos(
    candidates: list[dict],
    config: dict,
    goldset: "dict | None" = None,
) -> list[dict]:
    """Score, filter, and deduplicate repos by full_name, returning top N.

    Builds a cross-lane keyword term set (primary + all additional lanes) so
    that candidates surfaced by adjacent lanes are scored fairly against the
    full multi-lane vocabulary rather than only primary-lane keywords.

    Required gold-set repos (``required=true`` in the goldset file) are
    retained in the shortlist whenever they appear in *candidates* and their
    score is at or above their per-entry ``min_score`` (defaulting to the
    global shortlist ``min_score``). They preempt non-required repos within
    the ``max_candidates`` cap so they cannot be crowded out by higher-scored
    non-required repos. If the number of required repos itself exceeds the cap,
    the shortlist keeps the top-scoring required repos and logs a warning so
    operators can raise ``shortlist.max_candidates`` if needed.
    """
    sl_cfg = config.get("shortlist", {})
    max_n: int = int(sl_cfg.get("max_candidates", 5))
    min_score: float = float(sl_cfg.get("min_score", 0.15))
    exclude_forks: bool = bool(sl_cfg.get("exclude_forks", True))
    exclude_archived: bool = bool(sl_cfg.get("exclude_archived", False))
    # Never scout ourselves — exclude the target repo from candidates
    target_repo: str = config.get("target_repo", "").lower()

    # Build global cross-lane term set for consistent multi-lane scoring.
    global_terms = _build_global_term_set(config)

    # Load goldset for required-retention logic (auto-load from disk if not provided).
    if goldset is None:
        goldset = load_goldset()
    required_entries: dict[str, dict] = {}
    for entry in (goldset.get("entries") or []):
        if entry.get("required"):
            key = str(entry.get("repo", "")).lower()
            if key:
                required_entries[key] = entry

    seen: set[str] = set()
    scored: list[tuple[float, dict]] = []

    for repo in candidates:
        full_name: str = repo.get("full_name", "")
        if not full_name or full_name in seen:
            continue
        if full_name.lower() == target_repo:
            continue
        if exclude_forks and repo.get("fork"):
            continue
        if exclude_archived and repo.get("archived"):
            continue
        seen.add(full_name)
        s = score_repo(repo, config, term_set=global_terms)
        # Per-entry min_score for required repos; fall back to global min_score.
        repo_key = full_name.lower()
        if repo_key in required_entries:
            entry_min = required_entries[repo_key].get("min_score")
            effective_min = float(entry_min) if entry_min is not None else min_score
        else:
            effective_min = min_score
        if s >= effective_min:
            scored.append((s, repo))

    # Sort descending by score
    scored.sort(key=lambda t: t[0], reverse=True)

    # Separate required-goldset repos from regular candidates so they cannot
    # be crowded out by non-required repos within the top-N cap.
    pinned: list[tuple[float, dict]] = []
    rest: list[tuple[float, dict]] = []
    for item in scored:
        s, repo = item
        if repo.get("full_name", "").lower() in required_entries:
            pinned.append(item)
        else:
            rest.append(item)

    # Fill remaining slots: required repos first, then top-scoring rest.
    remaining_slots = max(0, max_n - len(pinned))
    combined = pinned + rest[:remaining_slots]
    # Re-sort combined result by score so the final list is score-ordered.
    combined.sort(key=lambda t: t[0], reverse=True)
    selected = combined[:max_n]

    selected_required = [
        repo for _, repo in selected
        if repo.get("full_name", "").lower() in required_entries
    ]
    if selected_required:
        selected_names = [repo.get("full_name") for repo in selected_required]
        if len(pinned) > len(selected_required):
            print(
                "  ⚠ Required gold-set repos exceed shortlist.max_candidates "
                f"({len(pinned)} > {max_n}); retaining top {len(selected_required)} "
                f"required repo(s): {selected_names}. Increase shortlist.max_candidates "
                "to keep all required repos.",
            )
        else:
            print(f"  📌 Retaining {len(selected_required)} required gold-set repo(s): {selected_names}")

    return [repo for _, repo in selected]


# ═══════════════════════════════════════════════════════════════════════════════
#  Issue Rendering
# ═══════════════════════════════════════════════════════════════════════════════

def _fmt_date(d: str) -> str:
    if not d:
        return "unknown"
    try:
        dt = datetime.fromisoformat(d.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return d


def _derive_problem(repo: dict, readme_excerpt: str) -> str:
    desc = (repo.get("description") or "").strip()
    if desc:
        return desc
    for line in readme_excerpt.splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "!", "[", ">")) and len(line) > 30:
            return line[:300]
    return "(No description available — review repo manually)"


def _derive_strengths(repo: dict) -> list[str]:
    out: list[str] = []
    stars: int = repo.get("stargazers_count", 0)
    forks: int = repo.get("forks_count", 0)
    topics: list[str] = repo.get("topics", [])
    lang: str = repo.get("language") or ""

    if stars >= 500:
        out.append(f"Strong community interest ({stars:,} ⭐)")
    elif stars >= 100:
        out.append(f"Solid community traction ({stars:,} ⭐)")
    elif stars >= 10:
        out.append(f"Growing community ({stars:,} ⭐)")

    if forks >= 50:
        out.append(f"Widely forked ({forks:,} forks) — active derivative work")
    elif forks >= 10:
        out.append(f"Moderate fork activity ({forks:,} forks)")

    if topics:
        out.append(f"Well-tagged: {', '.join(topics[:6])}")

    if lang:
        out.append(f"Primary language: {lang}")

    pushed = repo.get("pushed_at") or ""
    if pushed:
        try:
            age_days = (datetime.now(timezone.utc) -
                        datetime.fromisoformat(pushed.replace("Z", "+00:00"))).days
            if age_days < 30:
                out.append("Actively maintained (pushed within 30 days)")
            elif age_days < 90:
                out.append("Maintained (pushed within 90 days)")
        except Exception:
            pass

    return out or ["Insufficient metadata for strength analysis"]


def _derive_weaknesses(repo: dict) -> list[str]:
    out: list[str] = []
    stars: int = repo.get("stargazers_count", 0)
    open_issues: int = repo.get("open_issues_count", 0)

    if repo.get("archived"):
        out.append("⚠ Repository is archived — no active development expected")

    if repo.get("fork"):
        out.append("This is a fork — may not be the canonical/upstream source")

    if stars < 20:
        out.append(f"Low star count ({stars}) — limited community validation")

    pushed = repo.get("pushed_at") or ""
    if pushed:
        try:
            age_days = (datetime.now(timezone.utc) -
                        datetime.fromisoformat(pushed.replace("Z", "+00:00"))).days
            if age_days > 365:
                out.append(f"Inactive: last pushed {age_days} days ago (>1 year)")
            elif age_days > 180:
                out.append(f"Low activity: last pushed {age_days} days ago")
        except Exception:
            pass

    if open_issues > 50:
        out.append(f"High open issue count ({open_issues}) — may indicate maintenance backlog")

    if not repo.get("license"):
        out.append("No license detected — usage rights unclear")

    return out or ["No significant risks identified from available metadata"]


_MAX_HEURISTIC_LEARNINGS = 5  # cap to prevent wall-of-text bullet dumps

# Prefix of the generic fallback bullet returned by _derive_learnings() when
# no domain-specific signals fire.  Used by the Rowboat veto gate.
_VETO_FALLBACK_PREFIX = "Review the source for architectural patterns"
_IMPLEMENTED_LEARNING_HINTS: tuple[str, ...] = (
    "cli verb patterns",
    "structured reflexion workflow",
    "cross environment sync patterns",
    "git hook workflow patterns",
    "offline first design",
)


def _normalize_learning_text(text: str) -> str:
    """Normalize learning text for de-dup and heuristic quality checks."""
    lowered = text.lower()
    lowered = re.sub(r"`[^`]+`", " ", lowered)
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return " ".join(lowered.split())


def _is_already_implemented_learning(learning: str) -> bool:
    """Return True when a learning bullet is an already-shipped capability."""
    normalized = _normalize_learning_text(learning)
    return any(hint in normalized for hint in _IMPLEMENTED_LEARNING_HINTS)


def _dedupe_learning_bullets(learnings: list[str]) -> list[str]:
    """Drop duplicate learning bullets while preserving signal coverage."""
    seen: set[str] = set()
    filtered: list[str] = []
    for learning in learnings:
        bullet = learning.strip()
        if not bullet:
            continue
        sig = _normalize_learning_text(bullet)
        if sig in seen:
            continue
        seen.add(sig)
        filtered.append(bullet)
    return filtered


def _count_distinct_learning_signals(learnings: list[str]) -> int:
    """Count distinct insight families in a learnings list (fallback excluded)."""
    signatures: set[str] = set()
    for learning in learnings:
        if learning.startswith(_VETO_FALLBACK_PREFIX):
            continue
        heading_match = re.search(r"\*\*(.+?)\*\*", learning)
        if heading_match:
            signature = _normalize_learning_text(heading_match.group(1))
        else:
            signature = " ".join(_normalize_learning_text(learning).split()[:8])
        if signature:
            signatures.add(signature)
    return len(signatures)


def _derive_learnings(repo: dict, our_topics: list[str], readme_excerpt: str = "") -> list[str]:
    """Generate concrete, actionable learning bullets from a candidate repo.

    Each bullet encodes: the capability/pattern, what it could help with in this
    repo, and a specific example of where/how it could apply.

    Bullets are prioritised by how directly actionable they are for this repo's
    scripts, capped at _MAX_HEURISTIC_LEARNINGS to avoid wall-of-text dumps.
    Low-value generic bullets (novel-topic lists, alt-language comparisons,
    editor-keyword speculation) are not emitted.
    """
    out: list[str] = []
    repo_topics: set[str] = set(repo.get("topics", []))

    desc = (repo.get("description") or "").lower()
    # Combine description + first 3000 chars of readme for richer signal matching
    hint = f"{desc} {readme_excerpt[:3000].lower()}"

    # ── Claude Code integration (highest priority — directly affects claude-adapter.py) ──
    if "claude-code" in repo_topics or any(kw in hint for kw in (
        "claude code", "claude-code",
    )):
        out.append(
            "**Claude Code session patterns**: this repo's Claude Code integration approach "
            "could improve `claude-adapter.py`'s JSONL parsing — e.g., handling new session "
            "event types or extracting richer metadata from Claude Code tool-use blocks"
        )

    # ── Hybrid / semantic retrieval ──────────────────────────────────────────
    if any(kw in hint for kw in (
        "hybrid search", "fts+semantic", "fts + semantic", "semantic search",
        "vector search", "embedding", "ann ", "faiss", "hnswlib",
    )):
        out.append(
            "**Hybrid FTS+semantic retrieval**: combining keyword and embedding-based search "
            "could improve recall in `query-session.py` / `briefing.py` — e.g., a query for "
            "'docker networking' would also surface entries tagged 'container' or 'network_mode' "
            "even without exact term overlap"
        )

    # ── Knowledge graph / graph intelligence ────────────────────────────────
    if any(kw in hint for kw in (
        "graph intelligence", "knowledge graph", "knowledge-graph", "knowledge_graph",
    )) or "knowledge-graph" in repo_topics:
        out.append(
            "**Graph-based knowledge linking**: a relation graph over session entries could let "
            "`briefing.py` surface related decisions and mistakes by topic proximity — e.g., "
            "linking a `mistake:auth` record to `pattern:jwt` across separate session files "
            "without requiring identical keywords"
        )

    # ── Memory consolidation / dedup ─────────────────────────────────────────
    if any(kw in hint for kw in (
        "consolidate", "consolidat", "dream", "dedup", "deduplicate",
        "memory consolidat", "detect conflicts", "conflicts",
    )):
        out.append(
            "**Automated knowledge consolidation**: a background consolidation pass (like this "
            "repo's `dream` command) could extend `extract-knowledge.py` to merge near-duplicate "
            "learnings and flag contradicting patterns discovered across sessions"
        )

    # ── CLI ergonomics ────────────────────────────────────────────────────────
    if any(kw in hint for kw in (
        "memory-tool", "memory tool", "argparse", "typer",
    )) or bool(re.search(r"\bcli\b", hint)):
        out.append(
            "**CLI verb patterns**: a clear add/search/update/delete verb model (like "
            "`memory-tool add` / `search` / `dream`) could streamline the UX of "
            "`query-session.py` and `learn.py`, making them easier to invoke from hooks or scripts"
        )

    # ── Structured reflexion / reflection workflow ────────────────────────────
    if any(kw in hint for kw in (
        "reflexion", "reflect-load", "structured reflection", "structured refle",
        "post-mortem", "lessons learned",
    )):
        out.append(
            "**Structured reflexion workflow**: pre-task failure recall and post-task "
            "structured reflection (worked/failed/next fields) could extend `learn.py --mistake` "
            "and `briefing.py --auto` to support outcome-aware knowledge capture"
        )

    # ── Multi-agent coordination ──────────────────────────────────────────────
    if any(kw in hint for kw in (
        "multi-agent", "multi agent", "agent commons", "agent pool",
        "agents discover", "join rooms", "build trust",
    )):
        out.append(
            "**Multi-agent coordination**: the agent commons / trust model could inform how "
            "`learn.py` and `watch-sessions.py` handle concurrent writes from multiple "
            "simultaneous Copilot / Claude sessions"
        )

    # ── Zero-config / one-command install ────────────────────────────────────
    if any(kw in hint for kw in (
        "zero config", "zero-config", "one command", "1 command",
        "plugin marketplace", "pip install ai",
    )):
        out.append(
            "**Zero-config install UX**: `install.py` / `setup-project.py` could adopt a "
            "single-command bootstrap pattern (similar to this repo's one-command install) to "
            "lower the setup barrier when onboarding new machines or environments"
        )

    # ── Offline / no-cloud posture ────────────────────────────────────────────
    if any(kw in hint for kw in (
        "offline", "no cloud", "no-cloud", "owns your data", "zero api key", "zero api keys",
    )):
        out.append(
            "**Offline-first design**: this repo's no-cloud/no-server posture directly mirrors "
            "our local-SQLite approach — could validate that `knowledge.db` workflows never "
            "require external API calls even when semantic search is enabled"
        )

    # ── Cross-env sync ────────────────────────────────────────────────────────
    # Require an explicit cross-platform signal; bare "sync" is too common a word.
    if any(kw in hint for kw in ("cross-platform", "wsl", "windows")):
        out.append(
            "**Cross-environment sync patterns**: the sync strategy here could inform "
            "`sync-knowledge.py` for more robust Windows ↔ WSL knowledge merging"
        )

    # ── Hooks / workflow enforcement ──────────────────────────────────────────
    if any(kw in hint for kw in ("hook", "pre-commit", "workflow enforcement")):
        out.append(
            "**Git hook / workflow patterns**: hook design from this repo could strengthen the "
            "`hooks/` enforcement chain (e.g., auto-briefing, commit guards, learn reminders)"
        )

    # ── Export / portability ──────────────────────────────────────────────────
    # Note: "import" is intentionally excluded — it matches Python import statements
    # (e.g. "from ai_iq import Memory") and produces false positives for repos that
    # have no actual export/portability feature.
    if any(kw in hint for kw in ("export", "portable", "backup")):
        out.append(
            "**Knowledge portability**: the export/backup flow here could complement "
            "`sync-knowledge.py` for cross-machine knowledge portability"
        )

    # ── Editor integrations ───────────────────────────────────────────────────
    # Only fires when the repo is explicitly tagged with an editor topic — keyword
    # matches in descriptions/READMEs are too noisy (e.g. "cursor position", "vim-like").
    editor_topics = repo_topics & {"cursor", "vscode", "jetbrains", "neovim", "vim"}
    if editor_topics:
        editors = ", ".join(sorted(editor_topics))
        out.append(
            f"**Editor integration ({editors})**: `watch-sessions.py` could be extended to "
            f"detect and parse {editors} session formats natively, broadening the range of AI "
            f"sessions indexed into `knowledge.db`"
        )

    # ── MCP tool server ───────────────────────────────────────────────────────
    # Require "mcp server", "mcp-server", "model context protocol", or an explicit
    # "mcp" topic — bare substring matches on "mcp" are too noisy.
    if (
        "mcp server" in hint or "mcp-server" in hint
        or "model context protocol" in hint
        or "mcp" in repo_topics
    ):
        out.append(
            "**MCP tool-server surface**: the MCP server interface here could expose "
            "`query-session.py` and `briefing.py` as directly callable MCP tools — "
            "e.g., a Copilot agent could invoke the briefing lookup in-process rather "
            "than shelling out to a subprocess, reducing latency in hook-driven workflows"
        )

    # ── Multi-source ingestion / connector adapters ───────────────────────────
    if any(kw in hint for kw in (
        "confluence", "jira", "multi-source", "multi source",
        "ingestion adapter", "source connector", "data connector",
    )):
        out.append(
            "**Source connector / ingestion adapter**: the multi-source ingestion "
            "pattern here could inform a pluggable connector layer in "
            "`build-session-index.py` — e.g., separate adapters for Confluence pages, "
            "JIRA tickets, or Git history so external knowledge sources can be indexed "
            "into `knowledge.db` without changing core indexing logic"
        )

    # ── Frontmatter-aware markdown indexing ──────────────────────────────────
    if any(kw in hint for kw in (
        "frontmatter", "front matter", "front-matter", "obsidian",
    )):
        out.append(
            "**Frontmatter-aware indexing**: parsing YAML frontmatter in session "
            "markdown files could extend `build-session-index.py` and "
            "`extract-knowledge.py` to use structured metadata (category, tags, date) "
            "as indexed fields — e.g., frontmatter `category: mistake` could "
            "pre-populate the knowledge type without relying on regex heuristics"
        )

    # ── Incremental reindex / changed-file tracking ───────────────────────────
    # "reindex" is domain-specific enough on its own; compound phrases add coverage.
    if any(kw in hint for kw in (
        "incremental reindex", "incremental index", "reindex",
        "changed files", "change detection",
    )):
        out.append(
            "**Incremental reindex / changed-file tracking**: the change-detection "
            "approach here could improve `watch-sessions.py`'s polling loop — e.g., "
            "storing per-file content hashes so only modified or new session files "
            "trigger re-extraction, reducing redundant `extract-knowledge.py` passes "
            "on large session directories"
        )

    # ── Document / attachment conversion pipeline ─────────────────────────────
    if any(kw in hint for kw in (
        "document conversion", "file conversion", "attachment support",
        "html export", "office document",
    )):
        out.append(
            "**Document conversion pipeline**: the file/attachment ingestion approach "
            "here could extend `build-session-index.py` to normalise non-markdown "
            "sources — e.g., a pre-processing stage that converts attachments or "
            "exported documents to plain text before the FTS5 insert path"
        )

    # ── Fallback: concrete, architecture-specific ─────────────────────────────
    out = _dedupe_learning_bullets(out)

    if not out:
        out.append(
            "Review the source for architectural patterns applicable to FTS5 / knowledge-base "
            "workflows — particularly around data ingestion, search recall, and session indexing"
        )

    return out[:_MAX_HEURISTIC_LEARNINGS]


def _is_only_fallback_learnings(learnings: list[str]) -> bool:
    """Return True when the learnings list contains only the generic FTS5/architecture fallback.

    Used by the Rowboat veto gate: if True, no domain-specific signals fired and the
    candidate should be skipped rather than having generic filler written to an issue.
    """
    return len(learnings) == 1 and learnings[0].startswith(_VETO_FALLBACK_PREFIX)


def _should_veto_candidate(
    repo: dict, readme: str, our_topics: list[str], veto_cfg: dict,
    learnings: "list[str] | None" = None,
) -> tuple[bool, str]:
    """Rowboat veto gate: evaluate whether a candidate should be skipped.

    Returns (should_veto, reason). Vetoes when:
    - the heuristic learning engine produces only the generic fallback bullet
      (i.e., no domain-specific signals matched), and/or
    - configured distinct-insight quality gates are not met.

    If ``learnings`` is provided (e.g. pre-computed in ``create_stage``), it is
    used directly instead of re-running ``_derive_learnings``.  This ensures the
    production path and tests describe the same veto behaviour and that LLM-derived
    learnings are evaluated when available.
    """
    min_signals: int = int(veto_cfg.get("require_domain_signals", 0))
    min_distinct: int = int(veto_cfg.get("min_distinct_learnings", 0))
    if min_signals <= 0 and min_distinct <= 0:
        return False, ""
    effective = learnings if learnings is not None else _derive_learnings(repo, our_topics, readme)
    if min_signals > 0 and _is_only_fallback_learnings(effective):
        return True, "no domain-specific signals matched"
    if min_signals > 0:
        domain_signals = sum(1 for learning in effective if not learning.startswith(_VETO_FALLBACK_PREFIX))
        if domain_signals < min_signals:
            return True, f"insufficient domain signals ({domain_signals} < {min_signals})"
    if min_distinct > 0:
        novel = [learning for learning in effective if not _is_already_implemented_learning(learning)]
        distinct_signals = _count_distinct_learning_signals(novel)
        if distinct_signals < min_distinct:
            return True, f"insufficient distinct insights ({distinct_signals} < {min_distinct})"
    return False, ""
# ═══════════════════════════════════════════════════════════════════════════════

# Hard cap on individual LLM-generated bullet length to bound output size.
_MAX_LEARNING_BULLET_LEN = 600

# Prompt template for structured JSON output from the model.
_MODELS_PROMPT_TEMPLATE = (
    "You are reviewing a GitHub repository for a developer who maintains a Python "
    "AI coding-session knowledge base (SQLite FTS5, GitHub Copilot CLI integration, "
    "local-first design).\n\n"
    "Repository: {full_name}\n"
    "Description: {description}\n"
    "Topics: {topics}\n"
    "Primary language: {language}\n"
    "Stars: {stars}\n\n"
    "README excerpt (first 2000 chars):\n{readme}\n\n"
    "Our project's own topics: {our_topics}\n\n"
    "Task: Generate {max_learnings} concrete, actionable learning bullets explaining "
    "what our project can adopt or adapt from this repository. Each bullet should name "
    "a specific pattern, technique, or design decision and describe exactly how it could "
    "improve one of our scripts or workflows. Be specific — not generic.\n\n"
    'Respond with ONLY valid JSON, no markdown fences, no explanation:\n'
    '{{"learnings": ["**Pattern**: description...", "..."]}}'
)


def _is_valid_models_model_id(model: object) -> bool:
    """Return True when a model id matches GitHub Models' publisher/model format."""
    return isinstance(model, str) and bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", model.strip()))


def _analysis_value(analysis_cfg: dict | None, key: str, default: object) -> object:
    """Return an analysis setting, treating explicit null as "use default"."""
    if not isinstance(analysis_cfg, dict):
        return default
    value = analysis_cfg.get(key)
    return default if value is None else value


def _analysis_number(
    analysis_cfg: dict | None,
    key: str,
    default: int | float,
    parser: type[int] | type[float],
) -> int | float:
    """Parse an analysis numeric setting with explicit fallback logging."""
    raw = _analysis_value(analysis_cfg, key, default)
    try:
        return parser(raw)
    except (TypeError, ValueError):
        print(
            f"   Analysis: invalid {key}={raw!r} — using default {default}",
            flush=True,
        )
        return parser(default)


def _sanitize_learning_bullet(raw: object) -> str | None:
    """Validate and sanitise a single LLM-generated learning bullet.

    Returns the cleaned string, or None if the bullet should be rejected
    (wrong type, too short, contains HTML comment markers, etc.).
    """
    if not isinstance(raw, str):
        return None
    bullet = raw.strip()
    bullet = re.sub(r"\s*\n+\s*", " ", bullet).strip()
    if len(bullet) < 10:
        return None
    # Prevent HTML comment marker injection (could spoof dedup markers)
    if "<!--" in bullet or "-->" in bullet:
        return None
    # Strip any raw HTML tags the model may include
    bullet = re.sub(r"<[^>]+>", "", bullet)
    bullet = re.sub(r"[ \t]{2,}", " ", bullet).strip()
    if len(bullet) > _MAX_LEARNING_BULLET_LEN:
        bullet = bullet[:_MAX_LEARNING_BULLET_LEN] + "…"
    return bullet or None


def _analyze_repo_with_models(
    repo: dict,
    readme_excerpt: str,
    our_topics: list[str],
    client: "ModelsClient",
    model: str,
    temperature: float,
    max_tokens: int,
    max_learnings: int,
) -> list[str] | None:
    """Call the GitHub Models chat completions API to derive repo-specific learnings.

    Returns a sanitised list[str] on success, or **None** on any failure.
    Callers MUST fall back to ``_derive_learnings()`` when None is returned.

    Design notes:
    - Low temperature (default 0.2) keeps output deterministic across re-runs.
    - Structured JSON output is requested so parsing is explicit, not fragile.
    - Every bullet is sanitised before use; the whole batch is rejected if empty.
    - Markdown fences are stripped in case the model wraps its JSON anyway.
    """
    desc = (repo.get("description") or "").strip() or "(no description)"
    topics_str = ", ".join(repo.get("topics", [])) or "none"
    lang = repo.get("language") or "unknown"
    stars = repo.get("stargazers_count", 0)
    our_topics_str = ", ".join(our_topics) or "none"
    readme_snip = readme_excerpt[:2000].strip() or "(no README available)"

    prompt = _MODELS_PROMPT_TEMPLATE.format(
        full_name=repo.get("full_name", ""),
        description=desc[:300],
        topics=topics_str,
        language=lang,
        stars=stars,
        readme=readme_snip,
        our_topics=our_topics_str,
        max_learnings=max_learnings,
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You are a senior software architect. Respond only with the requested JSON. "
                "Do not include markdown code fences or any extra text."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    response = client.chat_completions(
        messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if response is None:
        return None

    # Extract text content from OpenAI-compatible response shape.
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        print(f"  ⚠ Models API: unexpected response shape ({exc}) — falling back", file=sys.stderr)
        return None
    if not isinstance(content, str):
        print("  ⚠ Models API: missing text content — falling back", file=sys.stderr)
        return None
    content = content.strip()
    if not content:
        print("  ⚠ Models API: empty text content — falling back", file=sys.stderr)
        return None

    # Strip accidental markdown fences the model may add despite instructions.
    content = re.sub(r"^```[a-zA-Z]*\n?", "", content)
    content = re.sub(r"\n?```$", "", content.strip())

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        print(f"  ⚠ Models API: JSON parse error ({exc}) — falling back", file=sys.stderr)
        return None

    if not isinstance(parsed, dict) or "learnings" not in parsed:
        print("  ⚠ Models API: missing 'learnings' key — falling back", file=sys.stderr)
        return None

    raw_bullets = parsed["learnings"]
    if not isinstance(raw_bullets, list):
        print("  ⚠ Models API: 'learnings' is not a list — falling back", file=sys.stderr)
        return None

    sanitized = [
        s for raw in raw_bullets[:max_learnings]
        if (s := _sanitize_learning_bullet(raw))
    ]

    if not sanitized:
        print("  ⚠ Models API: no valid bullets after sanitisation — falling back", file=sys.stderr)
        return None

    return sanitized


def render_issue_body(
    repo: dict,
    readme_excerpt: str,
    marker: str,
    our_topics: list[str],
    learnings: list[str] | None = None,
) -> str:
    """Build the structured issue body Markdown for a scouted repo.

    If ``learnings`` is provided (e.g. from the LLM analysis path) it is used
    directly; otherwise ``_derive_learnings()`` is called as the heuristic fallback.
    """
    full_name: str = repo["full_name"]
    html_url: str = repo.get("html_url") or f"https://github.com/{full_name}"

    problem = _derive_problem(repo, readme_excerpt)
    strengths = _derive_strengths(repo)
    weaknesses = _derive_weaknesses(repo)
    # Use pre-computed LLM learnings if provided; fall back to heuristic engine.
    effective_learnings = learnings if learnings is not None else _derive_learnings(repo, our_topics, readme_excerpt)

    license_name = (repo.get("license") or {}).get("spdx_id") or "None"
    topics_str = ", ".join(repo.get("topics", [])) or "none"

    readme_block = ""
    if readme_excerpt.strip():
        excerpt = readme_excerpt[:1500].strip()
        if len(readme_excerpt) > 1500:
            excerpt += "\n\n*(truncated)*"
        readme_block = (
            "\n<details>\n<summary>README excerpt</summary>\n\n"
            f"```\n{excerpt}\n```\n</details>\n"
        )

    strengths_md = "".join(f"- {s}\n" for s in strengths)
    weaknesses_md = "".join(f"- {w}\n" for w in weaknesses)
    learnings_md = "".join(f"- {l}\n" for l in effective_learnings)

    scouted_on = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    return (
        f"## 🔭 Trend Scout: [{full_name}]({html_url})\n\n"
        f"> Auto-generated by `trend-scout.py` — review and edit as needed.\n\n"
        f"### 📌 What problem it solves\n{problem}\n\n"
        f"### 📅 Timeline\n\n"
        f"| Field | Value |\n|---|---|\n"
        f"| Created | {_fmt_date(repo.get('created_at', ''))} |\n"
        f"| Last pushed | {_fmt_date(repo.get('pushed_at', ''))} |\n"
        f"| Stars | {repo.get('stargazers_count', 0):,} |\n"
        f"| Forks | {repo.get('forks_count', 0):,} |\n"
        f"| Open issues | {repo.get('open_issues_count', 0):,} |\n"
        f"| License | {license_name} |\n"
        f"| Language | {repo.get('language') or 'N/A'} |\n"
        f"| Topics | {topics_str} |\n\n"
        f"### ✅ Strengths\n{strengths_md}\n"
        f"### ⚠️ Weaknesses / Risks\n{weaknesses_md}\n"
        f"### 💡 What this repo can learn\n{learnings_md}"
        f"{readme_block}\n"
        f"---\n"
        f"*Scouted on {scouted_on} · [View on GitHub]({html_url})*\n\n"
        f"{marker}\n"
    )


_VOLATILE_PATTERNS = [
    # Footer: "*Scouted on 2025-07-17 · …*"
    re.compile(r"Scouted on \d{4}-\d{2}-\d{2}", re.IGNORECASE),
    # Weakness/strength bullets: "last pushed 42 days ago"
    re.compile(r"last pushed \d+ days ago", re.IGNORECASE),
]


def _strip_volatile_text(body: str) -> str:
    """Remove date/age substrings that change across calendar days so that
    body-equality checks are not tripped by purely cosmetic day-rollover churn.
    Real content changes (descriptions, scores, etc.) are unaffected."""
    for pat in _VOLATILE_PATTERNS:
        body = pat.sub("", body)
    return body


# ═══════════════════════════════════════════════════════════════════════════════
#  Run-state / Grace-window
# ═══════════════════════════════════════════════════════════════════════════════

_STATE_FILE_DEFAULT = SCRIPT_DIR / ".trend-scout-state.json"


def _resolve_state_file(run_control_cfg: dict) -> Path:
    """Resolve the state file path from config; defaults to script-adjacent file."""
    sf = run_control_cfg.get("state_file")
    if sf:
        return Path(sf).expanduser().resolve()
    return _STATE_FILE_DEFAULT


def load_run_state(state_file: "Path | None" = None) -> dict:
    """Load persisted run state from JSON file. Returns {} on any error."""
    path = state_file if state_file is not None else _STATE_FILE_DEFAULT
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def save_run_state(state: dict, state_file: "Path | None" = None) -> None:
    """Persist run state to JSON file. Silently ignores write errors."""
    path = state_file if state_file is not None else _STATE_FILE_DEFAULT
    try:
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"  ⚠ Could not save run state to {path}: {e}", file=sys.stderr)


def _check_grace_window(grace_window_hours: float, state: dict) -> tuple[bool, str]:
    """Check whether the last run falls within the grace window.

    Returns (skip, reason).  skip=True means the caller should skip this run.
    A grace_window_hours <= 0 always returns (False, "").
    """
    if grace_window_hours <= 0:
        return False, ""
    last_run_str: str = state.get("last_run_utc", "")
    if not last_run_str:
        return False, ""
    try:
        last_run = datetime.fromisoformat(last_run_str)
        elapsed_hours = (datetime.now(timezone.utc) - last_run).total_seconds() / 3600.0
        if elapsed_hours < grace_window_hours:
            remaining = grace_window_hours - elapsed_hours
            return True, (
                f"last run {elapsed_hours:.1f}h ago, "
                f"grace window {grace_window_hours:.0f}h "
                f"({remaining:.1f}h remaining)"
            )
    except Exception:
        pass
    return False, ""
# ═══════════════════════════════════════════════════════════════════════════════

def _run_single_lane(
    client: "GitHubClient",
    lane_name: str,
    keywords: list[str],
    topics: list[str],
    min_stars: int,
    max_per_query: int,
    created_after: "str | None",
    language: "str | None",
    collect_fn: "callable",
) -> dict:
    """Execute one discovery lane (keyword + topic searches).

    Calls ``collect_fn(batch, lane_name, query)`` for each search batch.
    Returns a lane stats dict: ``{name, keywords: [{query, count}], topics: [{query, count}]}``.
    """
    stats: dict = {"name": lane_name, "keywords": [], "topics": []}
    for kw in keywords:
        print(f"  🔍 [{lane_name}] Keyword: {kw!r}", flush=True)
        results = client.search_repos(
            kw,
            min_stars=min_stars,
            max_results=max_per_query,
            created_after=created_after,
            language=language,
        )
        stats["keywords"].append({"query": kw, "count": len(results)})
        collect_fn(results, lane_name, kw)
        time.sleep(1.2)
    for topic in topics:
        print(f"  🏷  [{lane_name}] Topic: {topic!r}", flush=True)
        results = client.search_repos_by_topic(
            topic, min_stars=min_stars, max_results=max_per_query, language=language,
        )
        stats["topics"].append({"query": topic, "count": len(results)})
        collect_fn(results, lane_name, topic)
        time.sleep(1.2)
    return stats


class DiscoveryResults(list):
    """List-like container for search results plus per-lane explain metadata."""

    def __init__(
        self,
        items: "list[dict] | None" = None,
        *,
        lane_stats: "list[dict] | None" = None,
    ) -> None:
        super().__init__(items or [])
        self.lane_stats = list(lane_stats or [])


def search_stage(client: "GitHubClient", config: dict) -> list[dict]:
    """Stage 1: collect raw candidate repos from GitHub Search API.

    Runs the primary lane (config["search"]) plus any additional lanes defined
    in config["lanes"].  Each repo is tagged with ``_discovery_lane`` and
    ``_discovery_query`` for downstream explainability.  Returns a list-like
    ``DiscoveryResults`` object carrying per-lane stats so explainability works
    even when zero candidates are found.
    """
    from datetime import timedelta

    s_cfg = config.get("search", {})
    min_stars: int = int(s_cfg.get("min_stars", 5))
    max_per_query: int = int(s_cfg.get("max_per_query", 10))
    lookback_days: int = int(s_cfg.get("lookback_days", 730))
    language: str = s_cfg.get("language", "")

    created_after = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime(
        "%Y-%m-%d"
    )

    all_repos: list[dict] = []
    seen_names: set[str] = set()
    all_lane_stats: list[dict] = []

    def _collect(batch: list[dict], lane_name: str, query: str) -> None:
        for repo in batch:
            fn = repo.get("full_name", "")
            if fn and fn not in seen_names:
                seen_names.add(fn)
                repo["_discovery_lane"] = lane_name
                repo["_discovery_query"] = query
                all_repos.append(repo)

    # ── Primary lane ──────────────────────────────────────────────────────────
    primary_keywords: list[str] = s_cfg.get("seed_keywords", [])
    primary_topics: list[str] = s_cfg.get("extra_topics", [])
    print(
        f"  🛤  Lane [primary] — {len(primary_keywords)} keyword(s), "
        f"{len(primary_topics)} topic(s)"
        + (f", language={language}" if language else ""),
        flush=True,
    )
    primary_stats = _run_single_lane(
        client, "primary", primary_keywords, primary_topics,
        min_stars, max_per_query, created_after, language or None, _collect,
    )
    all_lane_stats.append(primary_stats)

    # ── Additional lanes ──────────────────────────────────────────────────────
    for lane_cfg in config.get("lanes", []):
        lane_name = str(lane_cfg.get("name", "lane")).strip() or "lane"
        lane_keywords = [str(k) for k in lane_cfg.get("keywords", [])]
        lane_topics = [str(t) for t in lane_cfg.get("topics", [])]
        lane_min_stars = int(lane_cfg.get("min_stars", min_stars))
        lane_max_per_query = int(lane_cfg.get("max_per_query", max_per_query))
        lane_lookback_days = int(lane_cfg.get("lookback_days", lookback_days))
        lane_language = lane_cfg.get("language")
        if isinstance(lane_language, str):
            lane_language = lane_language.strip() or None
        lane_created_after = (
            datetime.now(timezone.utc) - timedelta(days=lane_lookback_days)
        ).strftime("%Y-%m-%d")
        print(
            f"  🛤  Lane [{lane_name}] — {len(lane_keywords)} keyword(s), "
            f"{len(lane_topics)} topic(s)"
            + (f", language={lane_language}" if lane_language else ", any language"),
            flush=True,
        )
        lane_stats = _run_single_lane(
            client, lane_name, lane_keywords, lane_topics,
            lane_min_stars, lane_max_per_query, lane_created_after, lane_language,
            _collect,
        )
        all_lane_stats.append(lane_stats)

    return DiscoveryResults(all_repos, lane_stats=all_lane_stats)


def build_discovery_explain(
    raw_repos: list[dict],
    shortlisted: list[dict],
    run_at: str,
    config: "dict | None" = None,
    goldset: "dict | None" = None,
) -> dict:
    """Build a discovery-explainability artifact from tagged search results.

    Returns a JSON-serialisable dict with per-lane query stats and shortlist
    annotations.  Suitable for saving as ``.trend-scout-discovery-explain.json``
    so operators can diagnose coverage gaps (e.g. the jcode-class miss).
    """
    lane_stats = getattr(raw_repos, "lane_stats", None)
    if not isinstance(lane_stats, list):
        lane_stats = []
        if raw_repos:
            lane_stats = raw_repos[0].get("_lane_stats", [])

    global_terms = _build_global_term_set(config or {}) if config else None

    raw_by_lane: dict[str, list[str]] = {}
    for repo in raw_repos:
        lane = repo.get("_discovery_lane", "primary")
        raw_by_lane.setdefault(lane, []).append(repo.get("full_name", ""))

    annotated_lanes = []
    for stats in lane_stats:
        name = stats.get("name", "primary")
        unique_count = len(raw_by_lane.get(name, []))
        kw_total = sum(q.get("count", 0) for q in stats.get("keywords", []))
        topic_total = sum(q.get("count", 0) for q in stats.get("topics", []))
        annotated_lanes.append({
            "name": name,
            "keywords": stats.get("keywords", []),
            "topics": stats.get("topics", []),
            "raw_hits": kw_total + topic_total,
            "unique_new_repos": unique_count,
        })

    artifact = {
        "run_at": run_at,
        "total_raw_candidates": len(raw_repos),
        "lanes": annotated_lanes,
        "shortlisted": [
            {
                "full_name": r.get("full_name", ""),
                "discovery_lane": r.get("_discovery_lane", "primary"),
                "discovery_query": r.get("_discovery_query", ""),
                "score": score_repo(r, config or {}, term_set=global_terms),
            }
            for r in shortlisted
        ],
        "coverage_note": (
            "Repos tagged with discovery_lane='adjacent-*' were surfaced by "
            "broader adjacent lanes, not primary-lane keyword/topic matching.  "
            "This distinguishes near-duplicate discovery (primary) from "
            "strategic-adjacency discovery (adjacent lanes)."
        ),
    }

    goldset_entries = []
    if isinstance(goldset, dict):
        goldset_entries = goldset.get("entries", [])
    if isinstance(goldset_entries, list) and goldset_entries:
        raw_index = {
            str(repo.get("full_name", "")).lower(): repo
            for repo in raw_repos
            if repo.get("full_name")
        }
        shortlisted_index = {
            str(repo.get("full_name", "")).lower(): repo
            for repo in shortlisted
            if repo.get("full_name")
        }
        goldset_rows = []
        raw_matches = 0
        shortlisted_matches = 0
        required_missing = 0
        lane_mismatches = 0
        score_failures = 0
        for entry in goldset_entries:
            repo_name = str(entry.get("repo", "") or "").strip()
            repo_key = repo_name.lower()
            raw_repo = raw_index.get(repo_key)
            shortlisted_repo = shortlisted_index.get(repo_key)
            source_repo = shortlisted_repo or raw_repo
            status = "missing"
            if shortlisted_repo is not None:
                status = "shortlisted"
                shortlisted_matches += 1
            if raw_repo is not None:
                status = "raw"
                raw_matches += 1
            if shortlisted_repo is not None:
                status = "shortlisted"

            score = None
            if source_repo is not None:
                score = score_repo(source_repo, config or {}, term_set=global_terms)

            expected_lane = entry.get("expected_lane")
            actual_lane = source_repo.get("_discovery_lane") if source_repo is not None else None
            lane_ok = None
            if expected_lane:
                if source_repo is not None:
                    lane_ok = actual_lane == expected_lane
                    if not lane_ok:
                        lane_mismatches += 1

            min_score = entry.get("min_score")
            score_ok = None
            if min_score is not None:
                if source_repo is not None:
                    score_ok = score is not None and score >= float(min_score)
                    if not score_ok:
                        score_failures += 1

            required = bool(entry.get("required", True))
            if required and source_repo is None:
                required_missing += 1

            goldset_rows.append({
                "repo": repo_name,
                "required": required,
                "category": entry.get("category"),
                "status": status,
                "found_in_raw": raw_repo is not None,
                "shortlisted": shortlisted_repo is not None,
                "expected_lane": expected_lane,
                "lane": actual_lane,
                "lane_ok": lane_ok,
                "score": score,
                "min_score": min_score,
                "score_ok": score_ok,
                "notes": entry.get("notes"),
            })

        artifact["goldset"] = {
            "path": goldset.get("path"),
            "expected_total": len(goldset_rows),
            "required_total": sum(1 for row in goldset_rows if row.get("required")),
            "found_in_raw": raw_matches,
            "found_in_shortlist": shortlisted_matches,
            "required_missing": required_missing,
            "lane_mismatches": lane_mismatches,
            "score_failures": score_failures,
            "entries": goldset_rows,
        }

    return artifact



def enrich_stage(repos: list[dict], client: GitHubClient, config: dict) -> list[tuple[dict, str]]:
    """Stage 3: fetch README for each shortlisted repo. Returns (repo, readme) pairs."""
    e_cfg = config.get("enrichment", {})
    fetch_readme: bool = bool(e_cfg.get("fetch_readme", True))
    readme_max: int = int(e_cfg.get("readme_max_chars", 3000))

    enriched: list[tuple[dict, str]] = []
    for repo in repos:
        readme = ""
        if fetch_readme:
            print(f"  📖 Fetching README: {repo['full_name']}", flush=True)
            raw = client.get_readme(repo["full_name"])
            readme = raw[:readme_max] if raw else ""
            time.sleep(0.5)
        enriched.append((repo, readme))
    return enriched


def create_stage(
    enriched: list[tuple[dict, str]],
    client: GitHubClient,
    config: dict,
    existing_markers: set[str],
    dry_run: bool = False,
    limit: int | None = None,
    models_client: "ModelsClient | None" = None,
    analysis_cfg: dict | None = None,
    issue_map: "dict[str, dict] | None" = None,
) -> list[str]:
    """Stage 5: render and create (or update) issues; returns list of issue URLs.

    If ``issue_map`` (marker → {number, state, body}) is provided, repos whose
    marker already exists follow marker-aware handling: open issues may be updated
    in place when content changed, while closed issues are treated as suppressors
    and skipped. Repos not present in the map follow the normal create path.

    If ``models_client`` is provided and ``analysis_cfg`` is non-empty, attempts to
    enrich each issue's learnings section via GitHub Models.  Falls back silently to
    the heuristic ``_derive_learnings()`` engine on any failure.
    """
    target_repo: str = config["target_repo"]
    label: str = config.get("issue_label", "trend-scout")
    label_color: str = config.get("issue_label_color", "0075ca")
    label_desc: str = config.get("issue_label_description", "Auto-generated trend scouting report")
    title_prefix: str = config.get("issue_title_prefix", "[Trend Scout]")
    marker_prefix: str = config.get("dedup", {}).get("marker_prefix", "trend-scout:repo:")
    our_topics: list[str] = config.get("search", {}).get("our_topics", [])
    analysis_model = str(_analysis_value(analysis_cfg, "model", DEFAULT_MODELS_MODEL)).strip() or DEFAULT_MODELS_MODEL
    analysis_temp = float(_analysis_number(analysis_cfg, "temperature", 0.2, float))
    analysis_max_tok = int(_analysis_number(analysis_cfg, "max_tokens", 800, int))
    analysis_max_learn = int(_analysis_number(analysis_cfg, "max_learnings", 5, int))
    # Rowboat veto gate: skip new creates when no domain-specific signals fire.
    veto_cfg: dict = config.get("veto", {})

    created_urls: list[str] = []
    created_count = 0

    for repo, readme in enriched:
        full_name: str = repo["full_name"]
        marker = repo_marker(full_name, marker_prefix)
        is_update = issue_map is not None and marker in issue_map
        existing_issue: dict | None = None
        issue_number: int | None = None
        issue_state: str = "open"
        existing_body: str = ""

        if is_update:
            existing_issue = issue_map[marker]  # type: ignore[index]
            issue_number = existing_issue["number"]
            issue_state = existing_issue.get("state", "open")
            if str(issue_state).lower() == "closed":
                print(f"  ⏭  Skip (closed marker suppresses writes): {full_name} #{issue_number}")
                continue
            existing_body = existing_issue.get("body") or ""

        if not is_update:
            # Skip repos already scouted when there is no update map entry.
            if marker in existing_markers:
                print(f"  ⏭  Skip (already scouted): {full_name}")
                continue
            # Limit only applies to new issue creates, not updates.
            # Use `continue` (not `break`) so update-eligible repos that appear
            # later in the list are still processed after the create cap is hit.
            if limit is not None and created_count >= limit:
                continue

        # ── Optional LLM-enhanced learnings (direction 2) ─────────────────────
        llm_learnings: list[str] | None = None
        if models_client is not None and analysis_cfg:
            print(f"  🤖 Analyzing with GitHub Models ({analysis_model})…", flush=True)
            llm_learnings = _analyze_repo_with_models(
                repo, readme, our_topics, models_client,
                model=analysis_model, temperature=analysis_temp,
                max_tokens=analysis_max_tok, max_learnings=analysis_max_learn,
            )
            if llm_learnings:
                print(f"  ✓ LLM learnings: {len(llm_learnings)} bullet(s)", flush=True)
            else:
                print("  ↩ Falling back to heuristic learnings engine", flush=True)

        # Compute effective learnings before veto check and rendering.
        effective_learnings: list[str] = (
            llm_learnings if llm_learnings is not None
            else _derive_learnings(repo, our_topics, readme)
        )

        # ── Rowboat veto gate (new creates only) ──────────────────────────────
        # Abstain rather than emit generic filler when no domain signals fired.
        if not is_update:
            _veto, _veto_reason = _should_veto_candidate(
                repo, readme, our_topics, veto_cfg, learnings=effective_learnings
            )
            if _veto:
                print(f"  ⊘ Veto ({_veto_reason}): {full_name}")
                continue

        title = f"{title_prefix} {full_name}"
        body = render_issue_body(repo, readme, marker, our_topics, learnings=effective_learnings)

        # ── Update path: existing issue found ─────────────────────────────────
        if is_update and issue_number is not None:
            if _strip_volatile_text(body.strip()) == _strip_volatile_text(existing_body.strip()):
                print(f"  ⏭  Skip (body unchanged): {full_name} #{issue_number}")
                continue

            if dry_run:
                print(f"\n  [dry-run] Would update issue #{issue_number}: {title!r}")
                print(f"  [dry-run] Target repo: {target_repo}")
                print(f"  [dry-run] State: {issue_state}")
                print(f"  [dry-run] Marker: {marker}")
                print(f"  [dry-run] Body preview ({len(body)} chars):")
                preview = body[:600].replace("\n", "\n    ")
                print(f"    {preview}")
                if len(body) > 600:
                    print(f"    … ({len(body) - 600} more chars)")
                created_urls.append(
                    f"[dry-run] https://github.com/{target_repo}/issues/{issue_number}"
                )
                continue

            print(f"  🔄 Updating issue #{issue_number}: {title!r}", flush=True)
            result = client.patch_issue(target_repo, issue_number, title, body)
            if result and isinstance(result, dict):
                url = result.get("html_url", "(no url)")
                print(f"  ✅ Updated: {url}")
                created_urls.append(url)
            else:
                print(f"  ✗ Failed to update issue #{issue_number} for {full_name}", file=sys.stderr)
            continue

        if dry_run:
            print(f"\n  [dry-run] Would create issue: {title!r}")
            print(f"  [dry-run] Target repo: {target_repo}")
            print(f"  [dry-run] Marker: {marker}")
            print(f"  [dry-run] Body preview ({len(body)} chars):")
            # Print first 600 chars of body
            preview = body[:600].replace("\n", "\n    ")
            print(f"    {preview}")
            if len(body) > 600:
                print(f"    … ({len(body) - 600} more chars)")
            created_urls.append(f"[dry-run] https://github.com/{target_repo}/issues/NEW")
            existing_markers.add(marker)  # prevent duplicate dry-run entries
            created_count += 1
            continue

        # Ensure label exists before creating first issue
        if created_count == 0:
            print(f"  🏷  Ensuring label {label!r} exists in {target_repo}…", flush=True)
            if not client.ensure_label(target_repo, label, label_color, label_desc):
                print(f"  ⚠ Could not ensure label '{label}' — will try without it", file=sys.stderr)

        print(f"  📝 Creating issue: {title!r}", flush=True)
        result = client.create_issue(target_repo, title, body, [label])
        if result and isinstance(result, dict):
            url = result.get("html_url", "(no url)")
            print(f"  ✅ Created: {url}")
            created_urls.append(url)
            existing_markers.add(marker)
            created_count += 1
        else:
            print(f"  ✗ Failed to create issue for {full_name}", file=sys.stderr)

    return created_urls


# ═══════════════════════════════════════════════════════════════════════════════
#  Entry Point
# ═══════════════════════════════════════════════════════════════════════════════

_EXPLAIN_OUTPUT_DEFAULT = SCRIPT_DIR / ".trend-scout-discovery-explain.json"


def _write_explain_artifact(
    raw_repos: list[dict],
    shortlisted: list[dict],
    config: dict,
    explain_output: "Path | None" = None,
) -> None:
    """Write the discovery explainability JSON to disk.

    The artifact captures per-lane query stats, raw candidate counts, and
    shortlist annotations.  Useful for CI replay assertions and operator
    triage of discovery coverage gaps.
    """
    run_at = datetime.now(timezone.utc).isoformat()
    goldset = load_goldset()
    artifact = build_discovery_explain(raw_repos, shortlisted, run_at, config=config, goldset=goldset)
    out_path = explain_output or _EXPLAIN_OUTPUT_DEFAULT
    try:
        out_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n  📊 Discovery explain artifact written → {out_path}", flush=True)
        lane_summary = ", ".join(
            f"{l['name']}:{l['unique_new_repos']}repos" for l in artifact.get("lanes", [])
        )
        print(f"     Lanes: {lane_summary}", flush=True)
        goldset_summary = artifact.get("goldset")
        if isinstance(goldset_summary, dict) and goldset_summary.get("expected_total", 0) > 0:
            print(
                "     Gold set: "
                f"raw {goldset_summary.get('found_in_raw', 0)}/{goldset_summary.get('expected_total', 0)} | "
                f"shortlisted {goldset_summary.get('found_in_shortlist', 0)}/{goldset_summary.get('expected_total', 0)} | "
                f"required missing {goldset_summary.get('required_missing', 0)}",
                flush=True,
            )
    except Exception as e:
        print(f"  ⚠ Could not write explain artifact to {out_path}: {e}", file=sys.stderr)


def run(config: dict, dry_run: bool = False, search_only: bool = False,
        limit: int | None = None, token: str | None = None, force: bool = False,
        explain: bool = False, explain_output: "Path | None" = None) -> int:
    """Execute the full trend-scout pipeline. Returns exit code.

    Args:
        explain: When True, writes a discovery explainability artifact to
            ``explain_output`` (default: ``.trend-scout-discovery-explain.json``
            adjacent to the script).  The artifact records per-lane query stats,
            raw candidate counts, and shortlist annotations so operators can
            diagnose coverage gaps (e.g. which lane found which repo).
    """
    target_repo = config["target_repo"]
    lanes_count = len(config.get("lanes", []))
    print(f"\n🔭 Trend Scout — target: {target_repo}")
    print(
        f"   Mode: {'dry-run' if dry_run else 'live'}{' [force]' if force else ''}"
        + (f" | lanes: 1 primary + {lanes_count} additional" if lanes_count else ""),
        flush=True,
    )

    # ── Grace window check ─────────────────────────────────────────────────────
    run_control_cfg: dict = config.get("run_control", {})
    grace_hours = float(run_control_cfg.get("grace_window_hours") or 0)
    state_file = _resolve_state_file(run_control_cfg)
    run_state: dict = {}
    if grace_hours > 0:
        run_state = load_run_state(state_file)
        if not force:
            skip, reason = _check_grace_window(grace_hours, run_state)
            if skip:
                print(f"⏭  Grace window active — {reason}")
                print(f"   Use --force to override.")
                return 0

    client = GitHubClient(token=token)

    # ── Optional GitHub Models client (direction 2) ───────────────────────────
    # Constructed only when analysis.enabled=true, the configured token exists,
    # and the model id matches GitHub Models' publisher/model format.
    models_client: ModelsClient | None = None
    analysis_cfg: dict = config.get("analysis", {})
    if analysis_cfg.get("enabled", False):
        token_env = str(_analysis_value(analysis_cfg, "token_env", "GITHUB_MODELS_TOKEN")).strip() or "GITHUB_MODELS_TOKEN"
        models_token = os.environ.get(token_env)
        model_name = str(_analysis_value(analysis_cfg, "model", DEFAULT_MODELS_MODEL)).strip() or DEFAULT_MODELS_MODEL
        if models_token:
            if not _is_valid_models_model_id(model_name):
                print(
                    f"   Analysis: invalid model id {model_name!r} "
                    "(expected 'publisher/model') — skipping LLM path",
                    flush=True,
                )
            else:
                endpoint = str(_analysis_value(analysis_cfg, "endpoint", MODELS_API_ENDPOINT)).strip() or MODELS_API_ENDPOINT
                timeout = int(_analysis_number(analysis_cfg, "timeout", 30, int))
                models_client = ModelsClient(token=models_token, endpoint=endpoint, timeout=timeout)
                print(f"   Analysis: GitHub Models enabled (model: {model_name})", flush=True)
        else:
            print(
                f"   Analysis: enabled in config but no token found in {token_env!r} "
                "— skipping LLM path",
                flush=True,
            )

    # Stage 1: Search
    print("\n[Stage 1/4] Searching GitHub for candidates…")
    candidates = search_stage(client, config)
    print(f"  → {len(candidates)} raw candidates found")

    # Stage 2: Shortlist
    print("\n[Stage 2/4] Shortlisting and scoring…")
    shortlisted = shortlist_repos(candidates, config)
    print(f"  → {len(shortlisted)} shortlisted repos:")
    for repo in shortlisted:
        s = score_repo(repo, config, term_set=_build_global_term_set(config))
        lane_tag = repo.get("_discovery_lane", "primary")
        print(f"    • {repo['full_name']} (score={s}, ⭐{repo.get('stargazers_count', 0)}, lane={lane_tag})")

    if search_only:
        if explain:
            _write_explain_artifact(candidates, shortlisted, config, explain_output)
        print("\n✅ --search-only: stopping before enrichment/issue creation.")
        return 0

    if not shortlisted:
        print("\nℹ Nothing shortlisted — no issues to create.")
        return 0

    # Stage 3: Fetch existing issue map for dedup + update
    print("\n[Stage 3/4] Fetching existing issue markers for deduplication…")
    issue_map = get_existing_issue_map(client, target_repo, config)
    existing_markers = set(issue_map.keys())
    print(f"  → Found {len(existing_markers)} existing marker(s)")

    # Stage 4: Enrich
    print("\n[Stage 4a] Enriching shortlisted repos…")
    enriched = enrich_stage(shortlisted, client, config)

    # Stage 5: Create/update issues (with optional LLM analysis per-repo)
    print("\n[Stage 4b] Creating/updating issues…")
    created = create_stage(
        enriched, client, config, existing_markers,
        dry_run=dry_run, limit=limit,
        models_client=models_client,
        analysis_cfg=analysis_cfg if models_client else None,
        issue_map=issue_map,
    )

    mode_tag = "[dry-run] " if dry_run else ""
    print(f"\n✅ Done — {mode_tag}{len(created)} issue(s) {'would be ' if dry_run else ''}created/updated.")

    # Write explain artifact if requested (includes final shortlist annotations).
    if explain:
        _write_explain_artifact(candidates, shortlisted, config, explain_output)

    # Persist last-run timestamp to enable grace-window protection on next run.
    if grace_hours > 0 and not dry_run and not search_only:
        new_state = {**run_state, "last_run_utc": datetime.now(timezone.utc).isoformat()}
        save_run_state(new_state, state_file)
        print(f"   Saved run state → {state_file}", flush=True)

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="GitHub-native daily trend scouting for copilot-session-knowledge",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python3 trend-scout.py --dry-run
  python3 trend-scout.py --search-only
  python3 trend-scout.py --repo owner/repo --limit 3
  GITHUB_TOKEN=ghp_... python3 trend-scout.py
""",
    )
    parser.add_argument("--config", default=None, metavar="PATH",
                        help=f"Config JSON file (default: {DEFAULT_CONFIG_PATH.name})")
    parser.add_argument("--repo", default=None, metavar="OWNER/REPO",
                        help="Override target issues repo (default from config)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview issue bodies without creating anything")
    parser.add_argument("--search-only", action="store_true",
                        help="Only run search + shortlist stages; print results and exit")
    parser.add_argument("--limit", type=int, default=None, metavar="N",
                        help="Maximum number of issues to create in one run")
    parser.add_argument("--token", default=None, metavar="TOKEN",
                        help="GitHub personal access token (overrides GITHUB_TOKEN env var)")
    parser.add_argument("--force", action="store_true",
                        help="Bypass grace window and force a new run regardless of last-run state")
    parser.add_argument("--explain", action="store_true",
                        help="Write a discovery explainability artifact (.trend-scout-discovery-explain.json) "
                             "showing per-lane query stats and shortlist annotations")
    parser.add_argument("--explain-output", default=None, metavar="PATH",
                        help="Path for the --explain artifact (default: .trend-scout-discovery-explain.json "
                             "adjacent to this script)")
    args = parser.parse_args()

    config_path = Path(args.config).resolve() if args.config else None
    config = load_config(config_path)

    if args.repo:
        if "/" not in args.repo:
            print(f"✗ --repo must be in 'owner/repo' format, got: {args.repo!r}")
            sys.exit(1)
        config["target_repo"] = args.repo

    explain_output = Path(args.explain_output).resolve() if args.explain_output else None

    sys.exit(run(
        config,
        dry_run=args.dry_run,
        search_only=args.search_only,
        limit=args.limit,
        token=args.token,
        force=args.force,
        explain=args.explain,
        explain_output=explain_output,
    ))


if __name__ == "__main__":
    main()
