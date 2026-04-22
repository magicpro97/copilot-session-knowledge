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
    ) -> list[dict]:
        """Search GitHub repos by topic tag."""
        q = f"topic:{topic} stars:>={min_stars}"
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


def score_repo(repo: dict, config: dict) -> float:
    """Compute a deterministic relevance score for a repo given config."""
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

    seed_keywords: list[str] = config.get("search", {}).get("seed_keywords", [])
    terms = _build_term_set(seed_keywords)
    matched = sum(1 for t in terms if t in search_text)
    score += matched * kw_weight

    # Topic overlap
    repo_topics: set[str] = set(repo.get("topics", []))
    config_topics: set[str] = set(config.get("search", {}).get("extra_topics", []))
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


def shortlist_repos(candidates: list[dict], config: dict) -> list[dict]:
    """Score, filter, and deduplicate repos by full_name, returning top N."""
    sl_cfg = config.get("shortlist", {})
    max_n: int = int(sl_cfg.get("max_candidates", 5))
    min_score: float = float(sl_cfg.get("min_score", 0.15))
    exclude_forks: bool = bool(sl_cfg.get("exclude_forks", True))
    exclude_archived: bool = bool(sl_cfg.get("exclude_archived", False))
    # Never scout ourselves — exclude the target repo from candidates
    target_repo: str = config.get("target_repo", "").lower()

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
        s = score_repo(repo, config)
        if s >= min_score:
            scored.append((s, repo))

    # Sort descending by score
    scored.sort(key=lambda t: t[0], reverse=True)
    return [repo for _, repo in scored[:max_n]]


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

    # ── Fallback: concrete, architecture-specific ─────────────────────────────
    if not out:
        out.append(
            "Review the source for architectural patterns applicable to FTS5 / knowledge-base "
            "workflows — particularly around data ingestion, search recall, and session indexing"
        )

    return out[:_MAX_HEURISTIC_LEARNINGS]


# ═══════════════════════════════════════════════════════════════════════════════
#  GitHub Models Analysis Path (direction 2)
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


# ═══════════════════════════════════════════════════════════════════════════════
#  Pipeline
# ═══════════════════════════════════════════════════════════════════════════════

def search_stage(client: GitHubClient, config: dict) -> list[dict]:
    """Stage 1: collect raw candidate repos from GitHub Search API."""
    s_cfg = config.get("search", {})
    keywords: list[str] = s_cfg.get("seed_keywords", [])
    topics: list[str] = s_cfg.get("extra_topics", [])
    min_stars: int = int(s_cfg.get("min_stars", 5))
    max_per_query: int = int(s_cfg.get("max_per_query", 10))
    lookback_days: int = int(s_cfg.get("lookback_days", 730))
    language: str = s_cfg.get("language", "")

    from datetime import timedelta
    created_after = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime(
        "%Y-%m-%d"
    )

    all_repos: list[dict] = []
    seen_names: set[str] = set()

    def _collect(batch: list[dict]) -> None:
        for repo in batch:
            fn = repo.get("full_name", "")
            if fn and fn not in seen_names:
                seen_names.add(fn)
                all_repos.append(repo)

    # Keyword searches (shortlist candidates first — do fewer heavier calls)
    for kw in keywords:
        print(f"  🔍 Keyword search: {kw!r}", flush=True)
        results = client.search_repos(
            kw,
            min_stars=min_stars,
            max_results=max_per_query,
            created_after=created_after,
            language=language or None,
        )
        _collect(results)
        # Respect GitHub Search API secondary rate limit (1 req/s for search)
        time.sleep(1.2)

    # Topic searches
    for topic in topics:
        print(f"  🏷  Topic search: {topic!r}", flush=True)
        results = client.search_repos_by_topic(topic, min_stars=min_stars, max_results=max_per_query)
        _collect(results)
        time.sleep(1.2)

    return all_repos


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
    marker already exists will have their issue body *updated in place* when the
    newly rendered content differs — without reopening closed issues.  Repos not
    present in the map follow the normal create path.

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

    created_urls: list[str] = []
    created_count = 0

    for repo, readme in enriched:
        full_name: str = repo["full_name"]
        marker = repo_marker(full_name, marker_prefix)
        is_update = issue_map is not None and marker in issue_map

        if not is_update:
            # Skip repos already scouted when there is no update map entry.
            if marker in existing_markers:
                print(f"  ⏭  Skip (already scouted): {full_name}")
                continue
            # Limit only applies to new issue creates, not updates.
            if limit is not None and created_count >= limit:
                break

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

        title = f"{title_prefix} {full_name}"
        # Pass llm_learnings (may be None → heuristic fallback inside render_issue_body)
        body = render_issue_body(repo, readme, marker, our_topics, learnings=llm_learnings)

        # ── Update path: existing issue found ─────────────────────────────────
        if is_update:
            existing_issue = issue_map[marker]  # type: ignore[index]
            issue_number: int = existing_issue["number"]
            issue_state: str = existing_issue.get("state", "open")
            existing_body: str = existing_issue.get("body") or ""

            if body.strip() == existing_body.strip():
                print(f"  ⏭  Skip (body unchanged): {full_name} #{issue_number}")
                continue

            if dry_run:
                print(f"\n  [dry-run] Would update issue #{issue_number}: {title!r}")
                print(f"  [dry-run] Target repo: {target_repo}")
                print(f"  [dry-run] State: {issue_state} (will NOT reopen if closed)")
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

def run(config: dict, dry_run: bool = False, search_only: bool = False,
        limit: int | None = None, token: str | None = None) -> int:
    """Execute the full trend-scout pipeline. Returns exit code."""
    target_repo = config["target_repo"]
    print(f"\n🔭 Trend Scout — target: {target_repo}")
    print(f"   Mode: {'dry-run' if dry_run else 'live'}", flush=True)

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
        s = score_repo(repo, config)
        print(f"    • {repo['full_name']} (score={s}, ⭐{repo.get('stargazers_count', 0)})")

    if search_only:
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
    args = parser.parse_args()

    config_path = Path(args.config).resolve() if args.config else None
    config = load_config(config_path)

    if args.repo:
        if "/" not in args.repo:
            print(f"✗ --repo must be in 'owner/repo' format, got: {args.repo!r}")
            sys.exit(1)
        config["target_repo"] = args.repo

    sys.exit(run(
        config,
        dry_run=args.dry_run,
        search_only=args.search_only,
        limit=args.limit,
        token=args.token,
    ))


if __name__ == "__main__":
    main()
