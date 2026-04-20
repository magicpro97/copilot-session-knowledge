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
        "readme_max_chars": 1500,
        "fetch_root_contents": False,
    },
    "dedup": {
        "marker_prefix": "trend-scout:repo:",
        "search_closed_issues": True,
        "max_issues_scan": 300,
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


def get_existing_markers(client: GitHubClient, target_repo: str, config: dict) -> set[str]:
    """Scan open (and optionally closed) issues labelled with the trend-scout label for markers."""
    dedup_cfg = config.get("dedup", {})
    marker_prefix: str = dedup_cfg.get("marker_prefix", "trend-scout:repo:")
    search_closed: bool = dedup_cfg.get("search_closed_issues", True)
    max_scan: int = int(dedup_cfg.get("max_issues_scan", 300))
    # Filter by label so we only scan issues that could carry markers; avoids
    # missing old markers on busy repos where unrelated issues dominate the page.
    label: str = config.get("issue_label", "trend-scout")

    markers: set[str] = set()
    states = ["open", "closed"] if search_closed else ["open"]

    for state in states:
        page = 1
        fetched = 0
        while fetched < max_scan:
            issues = client.list_issues(target_repo, state=state, per_page=100, page=page, labels=label)
            if not issues:
                break
            for issue in issues:
                # Skip PRs
                if issue.get("pull_request"):
                    continue
                body = issue.get("body") or ""
                markers |= extract_markers_from_body(body, marker_prefix)
            fetched += len(issues)
            if len(issues) < 100:
                break
            page += 1

    return markers


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


def _derive_learnings(repo: dict, our_topics: list[str]) -> list[str]:
    out: list[str] = []
    repo_topics: set[str] = set(repo.get("topics", []))
    our_set: set[str] = set(our_topics)
    novel_topics = repo_topics - our_set
    if novel_topics:
        out.append(f"Novel topics to explore: {', '.join(sorted(novel_topics)[:6])}")

    desc = (repo.get("description") or "").lower()
    readme_hint = desc

    if any(kw in readme_hint for kw in ("vector", "embedding", "ann", "faiss", "hnswlib")):
        out.append("Vector/embedding indexing strategies to compare with our FTS5 approach")
    if any(kw in readme_hint for kw in ("sync", "cross-platform", "wsl", "windows")):
        out.append("Cross-environment sync patterns worth studying")
    if any(kw in readme_hint for kw in ("cli", "command-line", "argparse", "typer")):
        out.append("CLI UX patterns applicable to our tooling")
    if any(kw in readme_hint for kw in ("hook", "git", "pre-commit", "workflow")):
        out.append("Git hook / workflow enforcement patterns")
    if any(kw in readme_hint for kw in ("export", "import", "portable", "backup")):
        out.append("Knowledge export/portability approaches")

    lang: str = repo.get("language") or ""
    if lang and lang.lower() not in ("python",):
        out.append(f"Alternative {lang} implementation — compare data structure choices")

    return out or ["Review architecture for patterns applicable to FTS5/knowledge-base workflows"]


def render_issue_body(repo: dict, readme_excerpt: str, marker: str, our_topics: list[str]) -> str:
    """Build the structured issue body Markdown for a scouted repo."""
    full_name: str = repo["full_name"]
    html_url: str = repo.get("html_url") or f"https://github.com/{full_name}"

    problem = _derive_problem(repo, readme_excerpt)
    strengths = _derive_strengths(repo)
    weaknesses = _derive_weaknesses(repo)
    learnings = _derive_learnings(repo, our_topics)

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
    learnings_md = "".join(f"- {l}\n" for l in learnings)

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
    readme_max: int = int(e_cfg.get("readme_max_chars", 1500))

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
) -> list[str]:
    """Stage 5: render and create issues; returns list of created issue URLs."""
    target_repo: str = config["target_repo"]
    label: str = config.get("issue_label", "trend-scout")
    label_color: str = config.get("issue_label_color", "0075ca")
    label_desc: str = config.get("issue_label_description", "Auto-generated trend scouting report")
    title_prefix: str = config.get("issue_title_prefix", "[Trend Scout]")
    marker_prefix: str = config.get("dedup", {}).get("marker_prefix", "trend-scout:repo:")
    our_topics: list[str] = config.get("search", {}).get("our_topics", [])

    created_urls: list[str] = []
    created_count = 0

    for repo, readme in enriched:
        if limit is not None and created_count >= limit:
            break

        full_name: str = repo["full_name"]
        marker = repo_marker(full_name, marker_prefix)

        if marker in existing_markers:
            print(f"  ⏭  Skip (already scouted): {full_name}")
            continue

        title = f"{title_prefix} {full_name}"
        body = render_issue_body(repo, readme, marker, our_topics)

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

    # Stage 3: Fetch existing markers for dedup
    print("\n[Stage 3/4] Fetching existing issue markers for deduplication…")
    existing_markers = get_existing_markers(client, target_repo, config)
    print(f"  → Found {len(existing_markers)} existing marker(s)")

    # Stage 4: Enrich
    print("\n[Stage 4a] Enriching shortlisted repos…")
    enriched = enrich_stage(shortlisted, client, config)

    # Stage 5: Create issues
    print("\n[Stage 4b] Creating issues…")
    created = create_stage(enriched, client, config, existing_markers, dry_run=dry_run, limit=limit)

    mode_tag = "[dry-run] " if dry_run else ""
    print(f"\n✅ Done — {mode_tag}{len(created)} issue(s) {'would be ' if dry_run else ''}created.")
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
