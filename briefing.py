#!/usr/bin/env python3
"""
briefing.py — Auto-generate context briefing from knowledge base

Before starting any task, run this to get relevant past experience injected as context.
AI agents can call this automatically to avoid repeating past mistakes.

Usage:
    python briefing.py "implement user CRUD"              # Compact briefing (default)
    python briefing.py "implement user CRUD" --full       # Full markdown briefing
    python briefing.py "fix Docker compose" --compact     # XML compact for AI context
    python briefing.py "fix Docker compose" --json        # JSON output
    python briefing.py "review auth PR" --mode review     # Mode-aware routing
    python briefing.py "debug flaky test_parser.py" --pack  # Compact machine JSON
    python briefing.py "spring boot migration" --limit 5  # More results per category
    python briefing.py --auto                             # Auto-detect from git/plan
    python briefing.py --auto --full                      # Full briefing with auto-detect
    python briefing.py --wakeup                           # Ultra-compact wake-up (~170 tokens)
    python briefing.py --room copyToGroup                 # Filter by room
    python briefing.py --wing backend                     # Filter by wing
    python briefing.py --titles-only                      # Progressive disclosure layer 1 (~10 tok/entry)
    python briefing.py --titles-only --limit 20           # More entries in titles mode
    python briefing.py "task desc" --budget 3000           # Cap output to 3000 chars (explicit override)
    python briefing.py --task "memory-surface"              # Task-scoped recall for a task ID
    python briefing.py "project" --budget 2000 --session-start  # sessionStart: Level 0 skill index + briefing
    python briefing.py "task" --available-tokens 40000     # Dynamic budget: 5% of context (≤2000 chars)
    python briefing.py "task" --since 2025-01-01           # Only entries seen on/after date
    python briefing.py "task" --days 30                    # Only entries seen in last 30 days
    python briefing.py "task" --feedback "task desc" good  # Record good feedback for a query
    python briefing.py "task" --feedback "task desc" bad   # Record bad feedback for a query
    python briefing.py "task" --pinned                     # Also show top-3 P0 pinned entries
    python briefing.py "task" --pinned 5                   # Also show top-5 P0 pinned entries
    python briefing.py "task" --no-repeat                  # Skip entries already served this session
    python briefing.py "task" --no-repeat=off              # Disable session-scoped deduplication
    python briefing.py "task" --no-dedup                   # Disable semantic near-duplicate collapse (issue #851)
    python briefing.py "task" --available-tokens 5000 --pressure-compact  # Auto-compact if < 20% context left

Default output is compact (~500 tokens): titles + 1-line summaries with entry IDs.
Use --titles-only for ultra-compact index (~10 tokens/entry). Then --detail <id> for full.
Use --wakeup for ultra-compact AI wake-up context (~170 tokens).
Use --full for complete content with tags, confidence scores, and full text.
Use --session-start (hook callers only) to prepend the Level 0 installed-skill index.
Use --available-tokens N to let briefing compute an effective budget dynamically from
context pressure (5% of N, capped at 2000 chars/~500 tokens) so output adapts to
context pressure automatically. Explicit --budget always takes precedence over --available-tokens.
"""

import dataclasses
import datetime
import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import textwrap
import time
from pathlib import Path

# Fix Windows console encoding
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TOOLS_DIR = Path(__file__).parent
SESSION_STATE = Path.home() / ".copilot" / "session-state"


@dataclasses.dataclass
class BriefingBudget:
    """Formal token budget allocator for sk briefing components.

    Inspired by yoheinakajima/babyagi3 context_budget.py.
    Each named slot has a maximum; knowledge_budget is whatever remains.
    """

    total_available: int = 8000
    response_reserve: int = 4096
    constitution_max: int = 1000
    code_context_max: int = 2000
    pinned_entries_max: int = 500
    danger_slot: int = 250

    @property
    def knowledge_budget(self) -> int:
        used = self.response_reserve + self.constitution_max + self.code_context_max + self.pinned_entries_max
        return max(500, self.total_available - used)

    @property
    def output_tier(self) -> str:
        """Auto-select output tier from total_available tokens.

        Tiers are based on total context size so small/medium/large contexts
        always route correctly regardless of slot configuration.
        """
        if self.total_available >= 16000:
            return "full"
        if self.total_available >= 6000:
            return "compact"
        return "titles"

    def describe(self) -> str:
        """Human-readable budget allocation summary."""
        return (
            f"Budget allocation (total={self.total_available}):\n"
            f"  response_reserve : {self.response_reserve}\n"
            f"  constitution_max : {self.constitution_max}\n"
            f"  code_context_max : {self.code_context_max}\n"
            f"  pinned_max       : {self.pinned_entries_max}\n"
            f"  knowledge_budget : {self.knowledge_budget}  → tier={self.output_tier}"
        )


DB_PATH = Path(os.environ.get("SK_DB_PATH", str(SESSION_STATE / "knowledge.db"))).expanduser()
_CLARIFY_STORE_PATH = SESSION_STATE / "clarifications.json"
_CONSTITUTION_RELATIVE_PATH = Path(".copilot") / "constitution.md"
_CONSTITUTION_RULE_RE = re.compile(r"\s*\[rule:[a-z0-9-]+\]\s*", re.IGNORECASE)


def _emit_knowledge_event_fail_open(event_type: str, data: dict) -> None:
    try:
        events_script = Path(__file__).with_name("events.py")
        if not events_script.is_file():
            return
        subprocess.call(
            [
                sys.executable,
                str(events_script),
                "append",
                event_type,
                "--data",
                json.dumps(data, ensure_ascii=False),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except Exception:
        return


class _ContextAwareHexMatcher:
    """Matches long lowercase-hex strings only when NOT in a git/checksum context.

    Prevents false-positive suppression of DB entries that legitimately reference
    git commit SHAs (40 hex chars) or SHA-256 checksums (64 hex chars).

    Duck-types the compiled regex interface: implements .search(text).
    """

    _HEX_RE = re.compile(r"(?<![A-Za-z0-9])([0-9a-f]{40,})(?![A-Za-z0-9])")
    _SAFE_CTX_RE = re.compile(r"(?i)\b(?:commit|sha\d*|hash|checksum|digest|fingerprint)\b")

    def search(self, text: str):
        """Return the first match for a secret-context hex run; None if all safe."""
        for m in self._HEX_RE.finditer(text):
            pre = text[max(0, m.start() - 100) : m.start()]
            if self._SAFE_CTX_RE.search(pre):
                continue
            return m
        return None


# WBS-014: Defense-in-depth credential/injection scan for briefing output.
# Suppress DB entries whose title or content contain credentials or injection
# patterns even if they slipped through WBS-013 extraction gate (e.g. from
# historical imports). Applied in generate_briefing() before entries reach
# any output formatter. Fail-open: scan errors allow the entry through.
_BRIEFING_UNSAFE_PATTERNS = [
    re.compile(r"(?i)\b(api[_-]?key|secret[_-]?key|password|token)\s*[:=]\s*\S{6,}"),
    re.compile(r"(?i)ssh-rsa\s+AAAA"),
    re.compile(r"(?i)-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"(?i)\bAuthorization\s*:\s*Bearer\s+\S{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\b(aws[_-]?secret[_-]?access[_-]?key|aws[_-]?secret)\s*[:=]\s*[A-Za-z0-9/+]{30,}"),
    _ContextAwareHexMatcher(),
    re.compile(r"(?i)\bignore\s+(all\s+)?previous\s+instructions?\b"),
]


def _briefing_entry_is_unsafe(entry: dict) -> bool:
    """Return True if a briefing entry contains credential/injection patterns.

    Used as a defense-in-depth read-side filter in generate_briefing() so that
    historical entries with leaked credentials are never emitted to agents even
    if WBS-013 didn't catch them at write time.  Fail-open: exceptions return
    False so the entry is included rather than silently dropped.
    """
    try:
        text = f"{entry.get('title', '')}\n{entry.get('content', '')}"
        return any(pat.search(text) for pat in _BRIEFING_UNSAFE_PATTERNS)
    except Exception:
        return False


# Read-side filter: suppress Wave-style progress/status-note entries that were
# mistakenly stored as knowledge (WaveN verification, rust-wave tentacle reports).
# These are project status updates, not actionable knowledge. Applied in
# _format_compact only — does not affect the DB or other output formats.
_STATUS_NOTE_RE = re.compile(
    r"""
    ^(?:
        Wave[-\s]?\d+\b            # "Wave14 …" or "Wave-14 …"
        .*?\b(?:verification\s+is\s+complete|phase[-\s\d]+\s+verification\s+is\s+complete)
        |                          # OR
        Wave[-\s]?\d+\b            # "Wave11 planner recommendation (not yet implemented)"
        .*?\(not\s+yet\s+implemented\)
        |                          # OR
        wave\d+[-\w]+\s+completed  # "wave6-pretooluse-deny completed …"
        |                          # OR
        \[rust-wave                # "[rust-wave7-hook-parity] …"
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Maximum entries per category rendered by _format_compact (issue #163).
# Entries are already relevance-ordered (confidence DESC + FTS rank) from
# generate_briefing; this cap keeps compact output at ≤ 3 entries per block,
# even when callers request a higher --limit for broader, non-compact surfaces.
# Status-note entries suppressed by _STATUS_NOTE_RE do NOT consume cap slots.
_COMPACT_MAX_PER_CAT = 3

BASE_CATEGORIES = {
    "mistake": {
        "emoji": "⚠️",
        "title": "Past Mistakes to Avoid",
        "desc": "These mistakes were encountered before. Avoid repeating them.",
    },
    "pattern": {"emoji": "✅", "title": "Proven Patterns to Follow", "desc": "These patterns worked well in the past."},
    "decision": {
        "emoji": "🏗️",
        "title": "Architecture Decisions",
        "desc": "Past decisions for reference — respect unless requirements changed.",
    },
    "tool": {
        "emoji": "🔧",
        "title": "Relevant Tools & Configs",
        "desc": "Tools and configurations used in similar work.",
    },
}

MODE_PROFILES = {
    "auto": {
        "order": ["mistake", "pattern", "decision", "tool"],
        "weights": {"mistake": 1.0, "pattern": 1.0, "decision": 1.0, "tool": 1.0},
    },
    "implement": {
        "order": ["pattern", "decision", "tool", "mistake"],
        "weights": {"mistake": 1.0, "pattern": 1.5, "decision": 1.3, "tool": 1.2},
    },
    "debug": {
        "order": ["mistake", "tool", "pattern", "decision"],
        "weights": {"mistake": 1.7, "pattern": 1.0, "decision": 0.9, "tool": 1.3},
    },
    "review": {
        "order": ["mistake", "pattern", "decision", "tool"],
        "weights": {"mistake": 1.6, "pattern": 1.3, "decision": 1.1, "tool": 0.9},
    },
    "plan": {
        "order": ["decision", "pattern", "mistake", "tool"],
        "weights": {"mistake": 1.0, "pattern": 1.2, "decision": 1.6, "tool": 0.8},
    },
    "test": {
        "order": ["mistake", "pattern", "tool", "decision"],
        "weights": {"mistake": 1.3, "pattern": 1.4, "decision": 0.9, "tool": 1.1},
    },
}

# ── Level 0 skill index constants (issue #118) ───────────────────────────────
# Total maximum description characters displayed in the skill index.
# When the source description exceeds this, it is truncated to the first 57
# characters followed by the three-character ASCII suffix "..." (total = 60).
_SKILL_DESC_MAX = 60


def _parse_skill_frontmatter(text: str) -> dict:
    """Parse YAML-ish frontmatter from a SKILL.md file using stdlib only.

    Extracts ``name`` and ``description`` from the first ``---``-delimited block.
    Handles folded YAML scalars (``description: >`` with indented continuation
    lines) without PyYAML.

    Returns a dict with keys ``name`` and ``description`` (may be empty strings
    on parse failure).  Never raises.
    """
    try:
        lines = text.replace("\r\n", "\n").split("\n")
        if not lines or lines[0].strip() != "---":
            return {"name": "", "description": ""}
        end = -1
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end = i
                break
        if end == -1:
            return {"name": "", "description": ""}
        fm_lines = lines[1:end]
        name = ""
        description = ""
        i = 0
        while i < len(fm_lines):
            line = fm_lines[i]
            stripped = line.strip()
            if stripped.startswith("name:"):
                name = stripped[len("name:") :].strip().strip('"').strip("'")
            elif stripped.startswith("description:"):
                rest = stripped[len("description:") :].strip()
                if rest in (">", "|", ">-", "|-", ">+", "|+"):
                    # Folded/literal block scalar — collect indented continuation lines.
                    desc_parts = []
                    i += 1
                    while i < len(fm_lines):
                        next_line = fm_lines[i]
                        if next_line and (next_line[0] in (" ", "\t")):
                            desc_parts.append(next_line.strip())
                        elif next_line.strip() == "":
                            desc_parts.append("")
                        else:
                            i -= 1
                            break
                        i += 1
                    description = " ".join(p for p in desc_parts if p)
                else:
                    description = rest.strip('"').strip("'")
            i += 1
        return {"name": name, "description": description}
    except Exception:
        return {"name": "", "description": ""}


def _generate_skill_index(skills_dir: "Path | None" = None) -> str:
    """Scan skills/*/SKILL.md and produce a Level 0 compact index string.

    Only called when ``--session-start`` is explicitly passed to ``briefing.py``
    (issue #118).  Returns an empty string when the skills directory is absent,
    has no readable SKILL.md files, or any error occurs (fail-open).

    Each entry is formatted as ``  <name> — <description>`` where description
    longer than ``_SKILL_DESC_MAX`` characters is truncated to 57 chars + ``"..."``
    (total ``_SKILL_DESC_MAX`` = 60 displayed characters).
    """
    try:
        base = skills_dir if skills_dir is not None else TOOLS_DIR / "skills"
        if not base.is_dir():
            return ""
        entries = []
        for skill_path in sorted(base.glob("*/SKILL.md")):
            try:
                text = skill_path.read_text(encoding="utf-8", errors="replace")
                meta = _parse_skill_frontmatter(text)
                skill_name = meta.get("name", "") or skill_path.parent.name
                desc = (meta.get("description", "") or "").strip()
                if len(desc) > _SKILL_DESC_MAX:
                    desc = desc[:57] + "..."
                entries.append((skill_name, desc))
            except Exception:
                continue  # fail-open per skill
        if not entries:
            return ""
        lines = [f"\U0001f4e6 Skills ({len(entries)} available):"]
        for skill_name, desc in entries:
            if desc:
                lines.append(f"  {skill_name} \u2014 {desc}")
            else:
                lines.append(f"  {skill_name}")
        lines.append("  " + "\u2500" * 33)
        return "\n".join(lines)
    except Exception:
        return ""  # always fail-open


def get_db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        print("Error: Knowledge database not found. Run build-session-index.py first.", file=sys.stderr)
        sys.exit(1)
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    return db


def _fetch_danger_lane(db: sqlite3.Connection, budget: int = 250, since_date: "str | None" = None) -> str:
    """Fetch critical mistake/antipattern entries for danger-lane section.

    Returns a formatted ⚠️ DANGER section string, or '' if no danger entries.
    """
    try:
        date_clause = ""
        params: list = []
        if since_date:
            date_clause = " AND (updated_at >= ? OR created_at >= ?)"
            params = [since_date, since_date]
        rows = db.execute(
            f"""SELECT title, content FROM knowledge_entries
               WHERE (category = 'mistake'
                  OR tags LIKE '%antipattern%'
                  OR tags LIKE '%security%'
                  OR tags LIKE '%breaking-change%')
               {date_clause}
               ORDER BY confidence DESC, priority ASC
               LIMIT 5""",
            params,
        ).fetchall()
    except sqlite3.OperationalError:
        return ""
    if not rows:
        return ""
    lines = ["⚠️  DANGER — always check before proceeding:"]
    remaining = budget
    for title, content in rows:
        snippet = f"  • {title}: {content[:80]}" if content else f"  • {title}"
        if len(snippet) > remaining:
            break
        lines.append(snippet)
        remaining -= len(snippet)
    if len(lines) <= 1:
        return ""
    return "\n".join(lines)


def _safe_int_list(values) -> list[int]:
    out = []
    for value in values:
        try:
            iv = int(value)
        except (TypeError, ValueError):
            continue
        out.append(iv)
    return out


def _detect_session_id() -> str:
    """Detect current session ID from environment or session-state path."""
    sid = os.environ.get("COPILOT_SESSION_ID", "")
    if sid:
        return sid
    # Try to extract from session-state path
    state_dir = os.environ.get("COPILOT_SESSION_STATE", "")
    if state_dir:
        return os.path.basename(state_dir)
    return ""


def _briefed_cache_path(session_id: str) -> Path:
    markers = Path.home() / ".copilot" / "markers"
    markers.mkdir(parents=True, exist_ok=True)
    return markers / f"session-briefed-{session_id}.json"


def _load_briefed_ids(session_id: str) -> set[int]:
    path = _briefed_cache_path(session_id)
    try:
        return set(json.loads(path.read_text()))
    except (OSError, json.JSONDecodeError, ValueError):
        return set()


def _save_briefed_ids(session_id: str, ids: set[int]) -> None:
    path = _briefed_cache_path(session_id)
    try:
        path.write_text(json.dumps(sorted(ids)))
    except OSError:
        pass  # fail-open


def _estimate_tokens(output_chars: int) -> int:
    return int(math.ceil(output_chars / 4)) if output_chars > 0 else 0


def _dedup_entries(entries: list, threshold: float = 0.85) -> list:
    """Remove near-duplicate entries using TF-IDF cosine similarity.

    Keeps the highest-confidence entry from each cluster.  Pure stdlib —
    no scikit-learn required.  Applied in compact mode to collapse entries
    that cover the same topic with different phrasing (issue #851).
    """
    if len(entries) <= 1:
        return entries

    from collections import Counter

    def _tfidf(texts: list[str]) -> list[dict]:
        tokenized = [set(t.lower().split()) for t in texts]
        N = len(texts)
        df: Counter = Counter(w for doc in tokenized for w in doc)
        idf = {w: math.log(N / (1 + df[w])) for w in df}
        vecs = []
        for doc in tokenized:
            tf: Counter = Counter(doc)
            total = len(doc) or 1
            vec = {w: (tf[w] / total) * idf[w] for w in doc}
            norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
            vecs.append({w: v / norm for w, v in vec.items()})
        return vecs

    texts = [f"{e.get('title', '')} {e.get('content', '')[:200]}" for e in entries]
    vecs = _tfidf(texts)

    def _cosine(a: dict, b: dict) -> float:
        return sum(a.get(w, 0.0) * b.get(w, 0.0) for w in a)

    kept: list = []
    used: set = set()
    for i in range(len(entries)):
        if i in used:
            continue
        cluster = [i]
        for j in range(i + 1, len(entries)):
            if j not in used and _cosine(vecs[i], vecs[j]) >= threshold:
                cluster.append(j)
                used.add(j)
        best = max(cluster, key=lambda k: entries[k].get("confidence", 0))
        if len(cluster) > 1:
            # Annotate the kept entry so renderers can show the merge notice
            kept_entry = dict(entries[best])
            merged_ids = [str(entries[k].get("id", "?")) for k in cluster if k != best]
            kept_entry["_merged_ids"] = merged_ids
            kept.append(kept_entry)
        else:
            kept.append(entries[best])
    return kept


def _check_pressure_compact(available_tokens: int, threshold: float = 0.20) -> bool:
    """Return True if context pressure warrants auto-compact instead of briefing."""
    context_size_hint = int(os.environ.get("SK_CONTEXT_SIZE", "128000"))
    sk_threshold = float(os.environ.get("SK_PRESSURE_THRESHOLD", str(threshold)))
    return available_tokens < sk_threshold * context_size_hint


def _run_pressure_compact(db_path: str, session_id: str | None = None) -> None:
    """Delegate to session-compact.py when under context pressure."""
    tools_dir = Path(__file__).parent
    compact_script = tools_dir / "session-compact.py"
    cmd = [sys.executable, str(compact_script)]
    if session_id:
        cmd += [session_id]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode == 0:
        print("[pressure-compact] Context pressure detected — running session compact:")
        print(result.stdout)
    else:
        print(f"[pressure-compact] Warning: compact failed — {result.stderr[:200]}", file=sys.stderr)
        # Fall through to normal briefing


def _compute_dynamic_budget(explicit_budget: int, available_tokens: int = 0) -> int:
    """Compute effective char-budget for briefing output (issue #125, #772).

    Priority order:
      1. If ``explicit_budget > 0``, return it unchanged (caller override wins).
      2. If ``available_tokens > 0``, derive knowledge_budget from BriefingBudget
         (issue #772: replaces the 5%-capped heuristic with formal slot allocation).
         When total_available < sum of all slots (raw surplus ≤ 0), fall back to
         the proportional heuristic so very small contexts still get proportionally
         small budgets rather than the 500-token floor.
      3. Otherwise return 0 (no budget cap — existing behaviour preserved).

    ``available_tokens`` is accepted from the caller via ``--available-tokens N``; briefing
    does not probe the context window itself, keeping the function deterministic and testable.
    """
    if explicit_budget > 0:
        return explicit_budget
    if available_tokens > 0:
        bb = BriefingBudget(total_available=available_tokens)
        used = bb.response_reserve + bb.constitution_max + bb.code_context_max + bb.pinned_entries_max
        raw_surplus = available_tokens - used
        if raw_surplus <= 0:
            # Context too small for full slot allocation; proportional fallback preserves
            # the small-budget enforcement that existing tests rely on (e.g. test 17m).
            return max(1, min(2000, int(available_tokens * 0.05)))
        # Convert token budget to chars (4 chars/token) for consistency with existing budget logic.
        return bb.knowledge_budget * 4
    return 0


def _record_recall_event(
    event_kind: str,
    surface: str,
    mode: str,
    raw_query: str,
    rewritten_query: str,
    task_id: str,
    selected_entry_ids: list[int],
    hit_count: int,
    output_chars: int,
    opened_entry_id: int | None = None,
) -> None:
    """Best-effort recall telemetry insert. Never crashes main surface."""
    payload = (
        time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        event_kind,
        "briefing",
        surface,
        mode or "",
        (raw_query or "")[:500],
        (rewritten_query or "")[:500],
        (task_id or "")[:200],
        "[]",
        json.dumps(_safe_int_list(selected_entry_ids), ensure_ascii=False),
        "[]",
        opened_entry_id,
        max(0, int(hit_count or 0)),
        max(0, int(output_chars or 0)),
        _estimate_tokens(max(0, int(output_chars or 0))),
    )
    db = None
    try:
        db = sqlite3.connect(str(DB_PATH))
        db.execute(
            """
            INSERT INTO recall_events (
                created_at, event_kind, tool, surface, mode,
                raw_query, rewritten_query, task_id, files,
                selected_entry_ids, selected_snippet_ids, opened_entry_id,
                hit_count, output_chars, output_est_tokens
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        db.commit()
    except sqlite3.OperationalError:
        pass
    finally:
        if db is not None:
            db.close()


def _upsert_entry_recall_stats(
    db: sqlite3.Connection,
    entry_ids: list[int],
    query: str,
) -> None:
    """Best-effort upsert of per-entry recall counters using the caller's open DB connection.

    Tracks recall_count (every recall), recall_days (unique calendar days), and
    unique_queries (unique rewritten queries) without double-counting repeats.
    Companion dedupe tables entry_recall_day_log and entry_recall_query_log prevent
    same-day / same-query inflation, and duplicate entry IDs in one call are
    collapsed so each surfaced entry counts once per recall pass. Never raises —
    fail-open by design.
    """
    normalized_entry_ids: list[int] = []
    seen_entry_ids: set[int] = set()
    for entry_id in _safe_int_list(entry_ids):
        if entry_id in seen_entry_ids:
            continue
        seen_entry_ids.add(entry_id)
        normalized_entry_ids.append(entry_id)

    if not normalized_entry_ids:
        return
    try:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        query_hash = hashlib.sha256((query or "").encode("utf-8", errors="replace")).hexdigest()[:16]
        now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        for eid in normalized_entry_ids:
            try:
                # Day dedupe — INSERT OR IGNORE; check rowcount to detect new day.
                db.execute(
                    "INSERT OR IGNORE INTO entry_recall_day_log (entry_id, day) VALUES (?, ?)",
                    (eid, today),
                )
                is_new_day = db.execute("SELECT changes()").fetchone()[0] > 0

                # Query dedupe — INSERT OR IGNORE; check rowcount to detect new unique query.
                db.execute(
                    "INSERT OR IGNORE INTO entry_recall_query_log (entry_id, query_hash) VALUES (?, ?)",
                    (eid, query_hash),
                )
                is_new_query = db.execute("SELECT changes()").fetchone()[0] > 0

                # Upsert main stats row — always bump recall_count; conditionally bump recall_days / unique_queries.
                day_delta = 1 if is_new_day else 0
                query_delta = 1 if is_new_query else 0
                db.execute(
                    """
                    INSERT INTO entry_recall_stats
                        (entry_id, recall_count, recall_days, unique_queries, first_recalled_at, last_recalled_at)
                    VALUES (?, 1, ?, ?, ?, ?)
                    ON CONFLICT(entry_id) DO UPDATE SET
                        recall_count = COALESCE(recall_count, 0) + 1,
                        recall_days = COALESCE(recall_days, 0) + excluded.recall_days,
                        unique_queries = COALESCE(unique_queries, 0) + excluded.unique_queries,
                        first_recalled_at = COALESCE(first_recalled_at, excluded.first_recalled_at),
                        last_recalled_at = excluded.last_recalled_at
                    """,
                    (eid, day_delta, query_delta, now, now),
                )
            except Exception:
                pass  # fail-open per-entry
        db.commit()
    except Exception:
        pass  # fail-open: per-entry telemetry is non-critical


def auto_detect_context() -> str:
    """Auto-detect task context — extract keywords from git + plan."""
    keywords = set()

    # Git branch name → extract feature keywords
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
        if branch and branch != "HEAD":
            # "feature/model-management" → "model management"
            parts = branch.replace("/", "-").replace("_", "-").split("-")
            keywords.update(p for p in parts if len(p) > 2 and p not in ("feature", "fix", "chore", "update", "and"))
    except Exception as e:
        print(f"⚠ Git branch detection failed: {e}", file=sys.stderr)

    # Git recent commit messages → extract subject words
    try:
        log = subprocess.run(
            ["git", "--no-pager", "log", "--oneline", "-5", "--format=%s"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
        if log:
            for line in log.splitlines():
                # Strip conventional commit prefix
                msg = line.split(":", 1)[-1].strip() if ":" in line else line
                words = msg.split()
                keywords.update(
                    w
                    for w in words
                    if len(w) > 2
                    and w.lower() not in ("the", "and", "for", "add", "fix", "update", "with", "from", "that")
                )
    except Exception as e:
        print(f"⚠ Git log parsing failed: {e}", file=sys.stderr)

    # Plan.md title/first meaningful line
    for session_dir in sorted(SESSION_STATE.iterdir(), reverse=True):
        plan = session_dir / "plan.md"
        if plan.exists():
            try:
                for line in plan.read_text(encoding="utf-8", errors="replace").splitlines()[:5]:
                    line = line.strip().lstrip("#").strip()
                    if line and not line.startswith("|") and len(line) > 5:
                        keywords.update(w for w in line.split() if len(w) > 2)
                        break
            except Exception as e:
                print(f"⚠ Plan file parsing failed: {e}", file=sys.stderr)
            break

    # Git modified file paths → extract module/feature names
    try:
        status = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
        if status:
            for fpath in status.splitlines()[:10]:
                parts = Path(fpath).parts
                keywords.update(p for p in parts if len(p) > 3 and not p.startswith(".") and "." not in p)
    except Exception as e:
        print(f"⚠ Git status parsing failed: {e}", file=sys.stderr)

    query = " ".join(sorted(keywords)[:15]) if keywords else "general development"
    return query


def _sanitize_fts_query(query: str, max_length: int = 500) -> str:
    """Sanitize user input for FTS5 MATCH queries."""
    query = query.strip()[:max_length]
    fts_special = set('"*(){}:^')
    cleaned = "".join(c if c not in fts_special else " " for c in query)
    terms = [t for t in cleaned.split() if t.upper() not in ("OR", "AND", "NOT", "NEAR")]
    if not terms:
        return '""'
    return " ".join(f'"{t}"*' for t in terms)


def _analyze_query_strictness(query: str) -> str:
    """Classify query retrieval strictness from lightweight signals.

    Returns 'strict', 'medium', or 'broad'.

    - 'strict':  1-2 terms, or has file/path separators or extensions, or
                 high average word length (domain-specific technical terms).
                 Callers use exact token matching and a tighter confidence threshold.
    - 'broad':   6+ words with 2+ natural-language stopwords present.
                 Callers use OR-conjunction matching and a relaxed threshold.
    - 'medium':  Everything else — the default prefix-match behaviour.

    No network or LLM calls.  Pure Python stdlib.
    """
    import re as _re

    words = query.strip().split()
    if not words:
        return "medium"

    wc = len(words)
    strict_score = 0
    broad_score = 0

    if wc <= 2:
        strict_score += 2
    elif wc >= 6:
        broad_score += 2

    # Technical path/identifier signals (file extensions, separators, long numeric IDs)
    _tech = _re.compile(r"\.[a-z]{1,5}(?:\b|$)|[/\\]|\d{4,}|_[a-z]")
    if any(_tech.search(w) for w in words):
        strict_score += 2

    avg_len = sum(len(w) for w in words) / wc
    if avg_len >= 7:
        strict_score += 1
    elif avg_len <= 3.5:
        broad_score += 1

    # Natural-language stopwords → query reads like a sentence → broad recall
    _STOPWORDS = frozenset(
        {
            "the",
            "for",
            "and",
            "with",
            "that",
            "this",
            "when",
            "how",
            "what",
            "why",
            "should",
            "use",
            "using",
            "from",
            "into",
            "over",
            "not",
            "does",
            "have",
            "are",
            "was",
            "we",
            "our",
            "they",
            "them",
            "it",
            "its",
            "by",
            "as",
            "at",
            "an",
            "a",
            "is",
            "in",
            "on",
            "to",
            "be",
            "or",
            "do",
            "so",
            "if",
        }
    )
    stopword_count = sum(1 for w in words if w.lower() in _STOPWORDS)
    if stopword_count >= 2:
        broad_score += 2

    if strict_score > broad_score:
        return "strict"
    if broad_score > strict_score:
        return "broad"
    return "medium"


def _build_adaptive_fts_query(query: str) -> tuple:
    """Build an FTS5 query and confidence-threshold delta based on query strictness.

    Returns:
        (fts_query: str, strictness: str, confidence_delta: float)

    Strictness effects:
        'strict' — exact token match (no trailing ``*``); confidence += 0.2.
                   Callers should fall back to prefix match when 0 results returned.
        'medium' — prefix match ``"term"*`` (current default); delta = 0.0.
        'broad'  — OR-conjunction prefix match for higher recall; confidence -= 0.2.
                   Common stopwords stripped from the OR terms to reduce noise.

    The confidence_delta is intended to be added to the caller's min_confidence
    (clamped to [0.0, 1.0]) so adaptive logic is non-breaking to existing contracts.
    """
    strictness = _analyze_query_strictness(query)
    base = _sanitize_fts_query(query)

    if base == '""':
        return base, strictness, 0.0

    terms = base.split()  # ["\"term\"*", ...]

    if strictness == "strict":
        # Strip trailing * to require exact token, not prefix
        fts_query = " ".join(t.rstrip("*") for t in terms)
        confidence_delta = 0.2
    elif strictness == "broad" and len(terms) > 1:
        # OR-conjunction: any term match is sufficient (recall over precision)
        _BROAD_STOPWORDS = frozenset(
            {
                "the",
                "for",
                "and",
                "with",
                "that",
                "this",
                "when",
                "how",
                "what",
                "why",
                "should",
                "use",
                "using",
                "from",
                "into",
                "over",
                "not",
                "does",
                "have",
                "are",
                "was",
                "we",
                "our",
                "they",
                "them",
                "it",
                "its",
                "by",
                "as",
                "at",
                "an",
                "a",
                "is",
                "in",
                "on",
                "to",
                "be",
                "or",
                "do",
                "so",
                "if",
            }
        )
        content_terms = [t for t in terms if t.strip('"*').lower() not in _BROAD_STOPWORDS]
        fts_query = " OR ".join(content_terms if content_terms else terms)
        confidence_delta = -0.2
    else:
        fts_query = base
        confidence_delta = 0.0

    return fts_query, strictness, confidence_delta


def _rewrite_query_local(query: str, max_terms: int = 15) -> str:
    """Conservative local query condensation while preserving technical tokens."""
    import re as _re

    if not query.strip():
        return query

    _SHORT_TECH = frozenset(
        {
            "go",
            "db",
            "ui",
            "js",
            "py",
            "io",
            "rx",
            "vm",
            "os",
            "ci",
            "cd",
            "tf",
            "qa",
        }
    )
    _FILLER = frozenset(
        {
            "please",
            "help",
            "me",
            "i",
            "need",
            "to",
            "for",
            "the",
            "a",
            "an",
            "and",
            "or",
            "with",
            "without",
            "that",
            "this",
            "these",
            "those",
            "in",
            "on",
            "at",
            "of",
            "from",
            "by",
            "about",
            "into",
            "it",
            "is",
            "are",
            "be",
            "can",
            "should",
            "would",
            "could",
            "how",
            "what",
            "why",
            "when",
            "where",
            "which",
            "want",
        }
    )

    raw_tokens = query.split()
    condensed = []
    seen = set()

    def _is_technical_token(tok: str) -> bool:
        if not tok:
            return False
        if any(c in tok for c in "/\\._-:#"):
            return True
        if any(c.isdigit() for c in tok):
            return True
        if _re.search(r"[a-z][A-Z]|[A-Z][a-z]", tok):
            return True
        if tok.isupper() and len(tok) > 1:
            return True
        return False

    for tok in raw_tokens:
        clean = tok.strip(" \t\r\n\"'`()[]{}<>.,;!?")
        if not clean:
            continue
        clean_lower = clean.lower()
        keep_short_tech = len(clean) == 2 and clean_lower in _SHORT_TECH
        if clean_lower in _FILLER and not keep_short_tech:
            continue
        keep_exact = _is_technical_token(clean)
        if not keep_exact and not keep_short_tech and len(clean) < 3:
            continue
        out_tok = clean if keep_exact else clean_lower
        if out_tok not in seen:
            condensed.append(out_tok)
            seen.add(out_tok)
        if len(condensed) >= max_terms:
            break

    return " ".join(condensed) if condensed else query.strip()


# ── Synonym expansion (issue #371) ────────────────────────────────────────────
# Conservative, domain-specific synonyms for knowledge-base retrieval.
_SYNONYM_MAP: dict[str, list[str]] = {
    "auth": ["auth", "authentication", "login", "token"],
    "authentication": ["authentication", "auth", "login", "token"],
    "error": ["error", "bug", "exception", "failure"],
    "bug": ["bug", "error", "issue", "defect"],
    "config": ["config", "configuration", "settings", "env"],
    "configuration": ["configuration", "config", "settings", "env"],
    "db": ["db", "database", "sqlite", "sql"],
    "database": ["database", "db", "sqlite", "sql"],
    "deploy": ["deploy", "deployment", "release", "publish"],
    "deployment": ["deployment", "deploy", "release", "publish"],
    "test": ["test", "tests", "testing", "spec"],
    "testing": ["testing", "test", "tests", "spec"],
    "perf": ["perf", "performance", "speed", "latency", "slow"],
    "performance": ["performance", "perf", "speed", "latency", "slow"],
    "cache": ["cache", "caching", "redis", "ttl", "invalidation"],
    "caching": ["caching", "cache", "redis", "ttl"],
    "migration": ["migration", "migrate", "schema", "upgrade"],
    "migrate": ["migrate", "migration", "schema", "upgrade"],
    "security": ["security", "vulnerability", "injection", "credential"],
    "search": ["search", "query", "retrieval", "fts", "fulltext"],
    "retrieval": ["retrieval", "search", "query", "recall", "fts"],
    "index": ["index", "indexing", "fts", "fts5"],
    "indexing": ["indexing", "index", "fts", "fts5"],
    "embedding": ["embedding", "vector", "semantic", "similarity"],
    "vector": ["vector", "embedding", "semantic", "similarity"],
    "session": ["session", "sessions", "history", "conversation"],
    "hook": ["hook", "hooks", "preToolUse", "postToolUse", "trigger"],
    "hooks": ["hooks", "hook", "preToolUse", "postToolUse", "trigger"],
    "api": ["api", "endpoint", "route", "rest", "http"],
    "endpoint": ["endpoint", "api", "route", "rest", "http"],
    "log": ["log", "logging", "logs", "output", "stderr"],
    "logging": ["logging", "log", "logs", "output"],
    "sync": ["sync", "synchronize", "push", "pull", "remote"],
    "synchronize": ["synchronize", "sync", "push", "pull", "remote"],
    "skill": ["skill", "skills", "plugin", "extension"],
    "skills": ["skills", "skill", "plugin", "extension"],
    "briefing": ["briefing", "context", "recall", "knowledge"],
    "knowledge": ["knowledge", "briefing", "recall", "learning"],
    "refactor": ["refactor", "refactoring", "rewrite", "cleanup"],
    "refactoring": ["refactoring", "refactor", "rewrite", "cleanup"],
}


def _expand_synonyms(query: str) -> str:
    """Expand query terms using domain synonym map (issue #371).

    Conservative expansion: only 1-6 token queries are expanded.
    Returns a space-joined string of unique expanded terms.
    Use _expand_synonyms_fts for FTS queries that need OR conjunction.
    """
    tokens = query.strip().split()
    if not tokens or len(tokens) > 6:
        return query.strip()

    expanded: list[str] = []
    seen: set[str] = set()
    for tok in tokens:
        clean = tok.lower().strip(" \t\"'.,;!?")
        synonyms = _SYNONYM_MAP.get(clean)
        if synonyms:
            for s in synonyms:
                if s not in seen:
                    expanded.append(s)
                    seen.add(s)
        else:
            if clean not in seen:
                expanded.append(clean)
                seen.add(clean)
    return " ".join(expanded) if expanded else query.strip()


def _expand_synonyms_fts(query: str) -> str:
    """Build an FTS5 OR query from synonym-expanded terms (issue #371).

    Unlike _expand_synonyms which returns space-joined terms (AND semantics
    in FTS5), this function builds an explicit OR-conjunction query so that
    any of the expanded synonyms triggers a match.  Safe to pass directly to
    a ``WHERE ke_fts MATCH ?`` parameter.
    """
    expanded = _expand_synonyms(query)
    _STRIP_OPS = frozenset({"OR", "AND", "NOT", "NEAR"})
    terms = [t for t in expanded.split() if t and t.upper() not in _STRIP_OPS]
    _FTS_SPECIAL = set('"*(){}:^')
    clean_terms = ["".join(c for c in t if c not in _FTS_SPECIAL) for t in terms]
    clean_terms = [t for t in clean_terms if t]
    if not clean_terms:
        return '""'
    return " OR ".join(f'"{t}"*' for t in clean_terms)


def _infer_mode_from_query(query: str) -> tuple[str, bool]:
    """Infer mode from query with conservative confidence gating."""
    q = query.lower()
    signal_map = {
        "implement": {"implement", "build", "create", "add", "feature", "integrate"},
        "debug": {"debug", "fix", "error", "bug", "trace", "failure", "exception", "broken"},
        "review": {"review", "audit", "inspect", "pr", "pull request", "security"},
        "plan": {"plan", "design", "approach", "strategy", "roadmap", "spec"},
        "test": {"test", "tests", "pytest", "unittest", "coverage", "assert"},
    }
    scored = []
    for mode, keys in signal_map.items():
        score = sum(1 for k in keys if k in q)
        if score:
            scored.append((score, mode))
    if not scored:
        return "auto", False
    scored.sort(reverse=True)
    top_score, top_mode = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0
    confident = top_score >= 2 or (top_score == 1 and second_score == 0 and len(query.split()) <= 8)
    return (top_mode if confident else "auto"), confident


def _resolve_mode_profile(mode: str, query: str, infer_auto: bool = True) -> tuple[str, dict]:
    """Resolve requested mode to an active mode/profile, conservative in auto."""
    mode = (mode or "auto").lower()
    if mode not in MODE_PROFILES:
        mode = "auto"
    if mode == "auto":
        if not infer_auto:
            return "auto", MODE_PROFILES["auto"]
        inferred, confident = _infer_mode_from_query(query)
        if confident and inferred in MODE_PROFILES:
            return inferred, MODE_PROFILES[inferred]
        return "auto", MODE_PROFILES["auto"]
    return mode, MODE_PROFILES[mode]


def _mode_category_config(limit: int, mode: str, query: str, infer_auto: bool = True) -> tuple[str, dict, dict]:
    """Compute active mode, ordered category metadata, and per-category limits."""
    active_mode, profile = _resolve_mode_profile(mode, query, infer_auto=infer_auto)
    order = [c for c in profile["order"] if c in BASE_CATEGORIES]
    categories = {cat: BASE_CATEGORIES[cat] for cat in order}
    per_cat_limit = {}
    for cat in categories:
        weight = profile["weights"].get(cat, 1.0)
        per_cat_limit[cat] = max(1, int(math.ceil(limit * weight)))
    return active_mode, categories, per_cat_limit


def _serialize_pack_entry(entry: dict) -> dict:
    entry_id = entry.get("id")
    try:
        related_ids = _related_entry_ids_for_entry(int(entry_id)) if entry_id is not None else []
    except (TypeError, ValueError):
        related_ids = []
    source_document = {
        "id": entry.get("document_id"),
        "doc_type": entry.get("source_doc_type"),
        "title": entry.get("source_doc_title"),
        "file_path": entry.get("source_doc_file_path"),
        "seq": entry.get("source_doc_seq"),
        "section": entry.get("source_section"),
    }
    return {
        "id": entry.get("id"),
        "title": entry.get("title", ""),
        "content": (entry.get("content", "") or "")[:500],
        "tags": entry.get("tags", ""),
        "confidence": entry.get("confidence", 0),
        "session_id": entry.get("session_id", ""),
        "occurrence_count": entry.get("occurrence_count", 0),
        "source_document": source_document,
        "source_file": entry.get("source_file"),
        "start_line": entry.get("start_line"),
        "end_line": entry.get("end_line"),
        "code_language": entry.get("code_language", ""),
        "code_snippet": (entry.get("code_snippet", "") or "")[:2000],
        "snippet_freshness": _compute_snippet_freshness(entry),
        "related_entry_ids": related_ids,
    }


def _row_value(row: dict, key: str, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        return default


def _source_document_from_row(row: dict) -> dict:
    return {
        "id": _row_value(row, "document_id"),
        "doc_type": _row_value(row, "source_doc_type"),
        "title": _row_value(row, "source_doc_title"),
        "file_path": _row_value(row, "source_doc_file_path"),
        "seq": _row_value(row, "source_doc_seq"),
        "section": _row_value(row, "source_section"),
    }


def _source_label_from_row(row: dict) -> str:
    doc_type = (_row_value(row, "source_doc_type") or "").strip()
    section = (_row_value(row, "source_section") or "").strip()
    seq = _row_value(row, "source_doc_seq")
    file_path = (_row_value(row, "source_doc_file_path") or "").strip()
    title = (_row_value(row, "source_doc_title") or "").strip()
    if not doc_type:
        return ""
    if doc_type == "checkpoint" and seq:
        base = f"from checkpoint #{seq}"
        return f"{base} / {section}" if section else base
    if file_path:
        base = Path(file_path).name
        label = f"from {doc_type} / {base}"
    elif title:
        label = f"from {doc_type} / {title[:60]}"
    else:
        label = f"from {doc_type}"
    return f"{label} / {section}" if section else label


def _code_location_label_from_row(row: dict) -> str:
    source_file = (_row_value(row, "source_file") or "").strip()
    if not source_file:
        return ""
    start_line = _row_value(row, "start_line")
    end_line = _row_value(row, "end_line")
    if start_line and end_line and start_line != end_line:
        return f"at {source_file}:{start_line}-{end_line}"
    if start_line:
        return f"at {source_file}:{start_line}"
    return f"at {source_file}"


def _normalize_feedback_query(query: str) -> str:
    """Canonical query normalization for feedback matching."""
    normalized = (query or "").lower()
    normalized = normalized.strip()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized[:500]


# ---------------------------------------------------------------------------
# Feedback write API (issue #707) — query-level feedback for briefing
# ---------------------------------------------------------------------------

_BRIEFING_VERDICT_MAP = {"good": 1, "bad": -1}


def write_feedback_query(query: str, verdict_str: str) -> None:
    """Insert a query-level feedback row into search_feedback.

    verdict_str: "good" (+1) or "bad" (-1).
    result_id is left empty for query-level feedback; result_kind is 'briefing'.
    """
    verdict = _BRIEFING_VERDICT_MAP.get(verdict_str)
    if verdict is None:
        print(f"Error: verdict must be one of: good, bad (got {verdict_str!r})")
        sys.exit(1)

    db_path = DB_PATH
    if not db_path.exists():
        print(f"Error: Knowledge database not found at {db_path}")
        sys.exit(1)

    import sqlite3 as _sq

    db = _sq.connect(str(db_path))
    db.row_factory = _sq.Row
    try:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='search_feedback'").fetchone()
        if not exists:
            print("Error: search_feedback table not found — run 'sk index migrate' to upgrade the DB")
            sys.exit(1)

        normalized = _normalize_feedback_query(query)
        created_at = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        db.execute(
            """
            INSERT INTO search_feedback (query, result_id, result_kind, verdict, created_at)
            VALUES (?, '', 'briefing', ?, ?)
            """,
            (normalized, verdict, created_at),
        )
        db.commit()
        label = {1: "good (+1)", -1: "bad (-1)"}[verdict]
        print(f"Briefing feedback recorded: query={normalized!r} → {label}")
    except _sq.OperationalError as exc:
        print(f"Error writing feedback: {exc}")
        sys.exit(1)
    finally:
        db.close()


def _clarify_query_tokens(query: str) -> set[str]:
    """Tokenize a clarification query for approximate matching."""
    return {
        match.group(0).lower() for match in re.finditer(r"[A-Za-z0-9_.:/-]+", query or "") if len(match.group(0)) >= 3
    }


def _load_clarification_entries(path: Path | None = None) -> list[dict]:
    """Load stored clarification results from session-state JSON."""
    target = path or _CLARIFY_STORE_PATH
    try:
        if not target.exists():
            return []
        data = json.loads(target.read_text(encoding="utf-8"))
        entries = data.get("entries", [])
        return [entry for entry in entries if isinstance(entry, dict)]
    except Exception:
        return []


def _clarification_match_score(query: str, repo_root: str, entry: dict) -> float:
    """Score a stored clarification entry against the current briefing query."""
    entry_repo_root = str(entry.get("repo_root", "") or "")
    if repo_root and entry_repo_root and entry_repo_root != repo_root:
        return -1.0

    query_norm = _normalize_feedback_query(query)
    entry_norm = _normalize_feedback_query(str(entry.get("normalized_query") or entry.get("raw_query") or ""))
    if not query_norm or not entry_norm:
        return -1.0
    if query_norm == entry_norm:
        return 3.0
    if query_norm in entry_norm or entry_norm in query_norm:
        return 2.0

    query_tokens = _clarify_query_tokens(query_norm)
    entry_tokens = set(entry.get("tokens", [])) or _clarify_query_tokens(entry_norm)
    if not query_tokens or not entry_tokens:
        return -1.0
    overlap = len(query_tokens & entry_tokens)
    if overlap <= 0:
        return -1.0
    return overlap / max(1, min(len(query_tokens), len(entry_tokens)))


def _load_matching_clarification(query: str, repo_root: str = "", path: Path | None = None) -> dict | None:
    """Return the best matching stored clarification for the current repo/query."""
    best_entry = None
    best_score = -1.0
    best_created_at = ""
    for entry in _load_clarification_entries(path):
        score = _clarification_match_score(query, repo_root, entry)
        created_at = str(entry.get("created_at", "") or "")
        if score > best_score or (score == best_score and created_at > best_created_at):
            best_entry = entry
            best_score = score
            best_created_at = created_at
    return best_entry if best_score > 0 else None


def _serialize_clarification(entry: dict | None) -> dict | None:
    """Return the stable public shape for a stored clarification entry."""
    if not entry:
        return None
    return {
        "raw_query": entry.get("raw_query", ""),
        "clarified_task": entry.get("clarified_task", ""),
        "questions": [
            {
                "category": question.get("category", ""),
                "question": question.get("question", ""),
                "options": list(question.get("options", [])),
            }
            for question in entry.get("questions", [])[:5]
            if isinstance(question, dict)
        ],
        "taxonomy_categories": list(entry.get("taxonomy_categories", [])),
        "created_at": entry.get("created_at", ""),
    }


def _strip_constitution_rule_tags(text: str) -> str:
    cleaned = _CONSTITUTION_RULE_RE.sub("", str(text or ""))
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def _load_constitution(repo_root: str = "") -> dict | None:
    root = Path(repo_root).resolve() if repo_root else _current_repo_root()
    constitution_path = root / _CONSTITUTION_RELATIVE_PATH
    if not constitution_path.is_file():
        return None
    sections = {
        "Principles": [],
        "Quality Gates": [],
        "Governance": [],
        "Changelog": [],
    }
    current_section = None
    title = "Project Constitution"
    version = ""
    last_amended = ""
    try:
        for raw_line in (
            constitution_path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n").split("\n")
        ):
            stripped = raw_line.strip()
            if not stripped:
                continue
            if stripped.startswith("# ") and title == "Project Constitution":
                title = stripped[2:].strip() or title
                continue
            lowered = stripped.lower()
            if lowered.startswith("version:"):
                version = stripped.split(":", 1)[1].strip()
                continue
            if lowered.startswith("last amended:"):
                last_amended = stripped.split(":", 1)[1].strip()
                continue
            if stripped.startswith("## "):
                section_name = stripped[3:].strip()
                current_section = section_name if section_name in sections else None
                continue
            if current_section and stripped.startswith("- "):
                sections[current_section].append(stripped[2:].strip())
    except OSError:
        return None

    return {
        "path": str(constitution_path),
        "title": title,
        "version": version,
        "last_amended": last_amended,
        "principles": sections["Principles"],
        "quality_gates": sections["Quality Gates"],
        "governance": sections["Governance"],
        "changelog": sections["Changelog"],
    }


def _serialize_constitution(entry: dict | None) -> dict | None:
    if not entry:
        return None
    return {
        "path": entry.get("path", ""),
        "title": entry.get("title", ""),
        "version": entry.get("version", ""),
        "last_amended": entry.get("last_amended", ""),
        "principles": [_strip_constitution_rule_tags(item) for item in entry.get("principles", [])[:5]],
        "quality_gates": [_strip_constitution_rule_tags(item) for item in entry.get("quality_gates", [])[:5]],
        "governance": [_strip_constitution_rule_tags(item) for item in entry.get("governance", [])[:5]],
        "changelog": list(entry.get("changelog", [])[:5]),
    }


def _xml_escape(text: str) -> str:
    """Escape a string for XML-like compact briefing output."""
    return str(text or "").replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def _compute_snippet_freshness(row: dict) -> str:
    """Read-time snippet freshness state: fresh|drifted|missing|unknown."""
    source_file = (_row_value(row, "source_file") or "").strip()
    code_snippet = _row_value(row, "code_snippet")

    if not source_file or code_snippet is None:
        return "unknown"

    start_line = _row_value(row, "start_line")
    end_line = _row_value(row, "end_line")
    if (
        not isinstance(start_line, int)
        or not isinstance(end_line, int)
        or start_line <= 0
        or end_line <= 0
        or end_line < start_line
    ):
        return "unknown"

    path = Path(source_file)
    if not path.exists() or not path.is_file():
        return "missing"

    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "unknown"

    content = content.replace("\r\n", "\n").replace("\r", "\n")
    lines = content.split("\n")
    if end_line > len(lines):
        return "unknown"

    current = "\n".join(lines[start_line - 1 : end_line])
    if not current:
        return "unknown"
    current_cmp = current.rstrip()
    stored_cmp = str(code_snippet).replace("\r\n", "\n").replace("\r", "\n").rstrip()
    if not stored_cmp:
        return "unknown"

    if stored_cmp.endswith("…"):
        prefix = stored_cmp[:-1]
        if not prefix:
            return "unknown"
        return "fresh" if current_cmp.startswith(prefix) else "drifted"
    return "fresh" if stored_cmp == current_cmp else "drifted"


def _related_entry_ids_for_entry(entry_id: int, db: sqlite3.Connection = None) -> list[int]:
    """Bidirectional related IDs with confidence-aware stable ordering."""
    own_db = False
    if db is None:
        db = get_db()
        own_db = True
    try:
        try:
            rows = db.execute(
                """
                SELECT target_id AS related_id, COALESCE(confidence, 0.0) AS rel_conf, 1 AS outgoing
                FROM knowledge_relations
                WHERE source_id = ?
                UNION ALL
                SELECT source_id AS related_id, COALESCE(confidence, 0.0) AS rel_conf, 0 AS outgoing
                FROM knowledge_relations
                WHERE target_id = ?
                """,
                (entry_id, entry_id),
            ).fetchall()
        except sqlite3.OperationalError:
            return []

        best: dict[int, tuple[float, int]] = {}
        for r in rows:
            rid = int(r["related_id"])
            conf = float(r["rel_conf"] if r["rel_conf"] is not None else 0.0)
            outgoing = int(r["outgoing"])
            prev = best.get(rid)
            if prev is None or conf > prev[0] or (conf == prev[0] and outgoing > prev[1]):
                best[rid] = (conf, outgoing)

        ranked = sorted(best.items(), key=lambda item: (-item[1][0], item[0]))
        return [int(rid) for rid, _ in ranked[:3]]
    finally:
        if own_db:
            db.close()


def _apply_feedback_bias_to_knowledge(
    db: sqlite3.Connection,
    query: str,
    entries: list[dict],
) -> list[dict]:
    """Feedback-aware reranking for knowledge entries.

    Applies two additive bias components:
    1. Feedback bias: up to ±0.15 based on past good/bad votes for this entry+query pair.
    2. Priority boost: +0.3 for P0 entries, +0.15 for P1 entries (issue #708).
    These biases operate on the normalised [0,1] score surface so they nudge order
    within a tier without overriding the primary composite-score ordering.
    """
    if not entries:
        return entries
    entry_ids = sorted({str(e.get("id")) for e in entries if e.get("id") is not None})
    if not entry_ids:
        return entries

    try:
        placeholders = ",".join("?" for _ in entry_ids)
        rows = db.execute(
            f"""
            SELECT query, result_id, verdict
            FROM search_feedback
            WHERE result_kind = 'knowledge'
              AND result_id IN ({placeholders})
            """,
            entry_ids,
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []

    normalized_query = _normalize_feedback_query(query)

    verdicts_by_id: dict[str, list[int]] = {}
    if normalized_query:
        for r in rows:
            row_q = _normalize_feedback_query(r["query"] or "")
            if row_q != normalized_query and row_q != "*":
                continue
            rid = str(r["result_id"] or "")
            verdicts_by_id.setdefault(rid, []).append(int(r["verdict"]))

    base_scores = [float(e.get("_semantic_score", 0.0)) for e in entries]
    if len(base_scores) <= 1:
        normalized_scores = [1.0 for _ in base_scores]
    else:
        min_score = min(base_scores)
        max_score = max(base_scores)
        if max_score == min_score:
            normalized_scores = [1.0 for _ in base_scores]
        else:
            span = max_score - min_score
            normalized_scores = [(score - min_score) / span for score in base_scores]

    # Priority boost constants (issue #708)
    _PRIORITY_BOOST = {"P0": 0.3, "P1": 0.15}

    def _bias_for(entry_id: str) -> float:
        votes = verdicts_by_id.get(entry_id, [])
        if not votes:
            return 0.0
        non_neutral = [v for v in votes if v != 0]
        if not non_neutral:
            return 0.0
        feedback_sum = sum(non_neutral)
        return max(-0.15, min(0.15, feedback_sum * 0.05))

    def _priority_boost_for(entry: dict) -> float:
        return _PRIORITY_BOOST.get(entry.get("priority") or "P2", 0.0)

    ranked = []
    for idx, entry in enumerate(entries):
        base = normalized_scores[idx]
        bias = _bias_for(str(entry.get("id")))
        pboost = _priority_boost_for(entry)
        ranked.append((base + bias + pboost, idx, entry))

    ranked.sort(key=lambda x: (-x[0], x[1]))
    out = []
    for _, _, entry in ranked:
        clean = dict(entry)
        clean.pop("_semantic_score", None)
        out.append(clean)
    return out


def _extract_task_matches(db: sqlite3.Connection, rewritten_query: str, limit: int = 5) -> list[dict]:
    """Find likely task-level matches for pack output."""
    terms = [t.strip('"*') for t in _sanitize_fts_query(rewritten_query).split() if t.strip('"*')]
    seen = set()
    out = []
    for term in terms[:8]:
        try:
            rows = db.execute(
                """
                SELECT task_id, title, category, confidence
                FROM knowledge_entries
                WHERE task_id != ''
                  AND (task_id LIKE ? OR title LIKE ?)
                ORDER BY confidence DESC, occurrence_count DESC
                LIMIT ?
            """,
                (f"%{term}%", f"%{term}%", limit),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        for r in rows:
            tid = r["task_id"]
            if not tid or tid in seen:
                continue
            seen.add(tid)
            out.append(
                {
                    "task_id": tid,
                    "title": r["title"] or "",
                    "category": r["category"] or "",
                    "confidence": r["confidence"] or 0,
                }
            )
            if len(out) >= limit:
                return out
    return out


def _extract_file_matches(db: sqlite3.Connection, rewritten_query: str, limit: int = 5) -> list[dict]:
    """Find likely file/module matches for pack output."""
    import re as _re

    tokens = []
    for raw in rewritten_query.split():
        tok = raw.strip(" \t\r\n\"'`()[]{}<>.,;!?")
        if not tok:
            continue
        if _re.search(r"/|\\|\.[a-zA-Z0-9]{1,6}$|_|-", tok):
            tokens.append(tok)
    seen = set()
    out = []
    for tok in tokens[:8]:
        try:
            rows = db.execute(
                """
                SELECT id, title, category, confidence
                FROM knowledge_entries
                WHERE affected_files LIKE ? OR content LIKE ? OR title LIKE ?
                ORDER BY confidence DESC, occurrence_count DESC
                LIMIT ?
            """,
                (f"%{tok}%", f"%{tok}%", f"%{tok}%", limit),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        hits = 0
        for r in rows:
            key = f"{tok}:{r['id']}"
            if key in seen:
                continue
            seen.add(key)
            hits += 1
        if hits:
            out.append({"file_or_module": tok, "hits": hits})
        if len(out) >= limit:
            break
    return out


def _extract_next_open(limit: int = 5) -> list[dict]:
    """Reserved for future external todo integration; stable empty for now."""
    _ = limit
    return []


def _ke_has_intensity(db: sqlite3.Connection) -> bool:
    """Return True if knowledge_entries has the intensity column (v21 migration applied)."""
    try:
        cols = {row[1] for row in db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
        return "intensity" in cols
    except Exception:
        return False


def _ke_has_priority(db: sqlite3.Connection) -> bool:
    """Return True if knowledge_entries has the priority column (v22 migration applied)."""
    try:
        cols = {row[1] for row in db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
        return "priority" in cols
    except Exception:
        return False


def _fetch_pinned_p0_entries(db: sqlite3.Connection, limit: int = 3) -> list[dict]:
    """Return top-N P0 knowledge entries sorted by composite score (issue #708 --pinned).

    These entries are always included in briefings when --pinned is active,
    regardless of the query. Returns an empty list when the priority column
    is absent (pre-v22 DB) or when no P0 entries exist.
    """
    if not _ke_has_priority(db):
        return []
    try:
        rows = db.execute(
            """
            SELECT ke.id, ke.title, ke.content, ke.tags, ke.category,
                   ke.confidence, COALESCE(ke.intensity, 0.5) AS intensity,
                   ke.last_seen, COALESCE(ke.priority, 'P2') AS priority
            FROM knowledge_entries ke
            WHERE ke.priority = 'P0'
            ORDER BY COALESCE(ke.intensity, 0.5) DESC, ke.confidence DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _ke_has_is_resolved(db: sqlite3.Connection) -> bool:
    """Return True if knowledge_entries has the is_resolved column (lifecycle migration applied)."""
    try:
        cols = {row[1] for row in db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
        return "is_resolved" in cols
    except Exception:
        return False


def _ke_has_recurrence(db: sqlite3.Connection) -> bool:
    """Return True if knowledge_entries has the recurrence_after_briefing column."""
    try:
        cols = {row[1] for row in db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
        return "recurrence_after_briefing" in cols
    except Exception:
        return False


def _ke_has_last_accessed(db: sqlite3.Connection) -> bool:
    """Return True if knowledge_entries has the last_accessed_at column (v36 migration applied)."""
    try:
        cols = {row[1] for row in db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
        return "last_accessed_at" in cols
    except Exception:
        return False


def _intensity_order_expr(alias: str = "ke", has_priority: bool = False) -> str:
    """SQL ORDER BY expression that ranks entries by priority then intensity.

    When priority column is present (v22+), priority is the primary sort key.
    Intensity is secondary so a high-intensity entry outranks lower-intensity ones
    within the same priority bucket.  Confidence and FTS rank are final tiebreakers.
    """
    if has_priority:
        priority_expr = (
            f"CASE COALESCE({alias}.priority, 'P2') "
            "WHEN 'P0' THEN 3 WHEN 'P1' THEN 2 WHEN 'P2' THEN 1 WHEN 'P3' THEN 0 ELSE 1 END DESC"
        )
        return f"{priority_expr}, COALESCE({alias}.intensity, 0.5) DESC, {alias}.confidence DESC, rank"
    return f"COALESCE({alias}.intensity, 0.5) DESC, {alias}.confidence DESC, rank"


def _recency_decay(last_seen_str: str | None, half_life_days: float = 30.0) -> float:
    """Exponential decay weight for an entry's age.

    Returns a value in (0, 1]: 1.0 for a just-created entry, approaching 0 for
    a very old one.  An entry exactly ``half_life_days`` old scores 0.5.
    Returns 1.0 (fail-open) when the timestamp is absent or unparseable.
    """
    if not last_seen_str or half_life_days <= 0:
        return 1.0
    try:
        ts_str = str(last_seen_str)[:19].replace("T", " ")
        ts = datetime.datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        now_utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        age_days = max(0.0, (now_utc - ts).total_seconds() / 86400.0)
        return 0.5 ** (age_days / half_life_days)
    except Exception:
        return 1.0


def _decay_weight(last_accessed_at: str, half_life_days: int = 90) -> float:
    """Ebbinghaus exponential decay: exp(-ln(2) * days_since / half_life).

    Returns 1.0 if last_accessed_at is empty (never accessed = no decay penalty yet).
    Returns value in (0, 1] based on recency of last access.
    """
    import math

    if not last_accessed_at:
        return 1.0
    try:
        last = datetime.datetime.fromisoformat(last_accessed_at.replace("Z", "+00:00"))
        if last.tzinfo is None:
            last = last.replace(tzinfo=datetime.timezone.utc)
        now = datetime.datetime.now(datetime.timezone.utc)
        days_since = max(0, (now - last).total_seconds() / 86400)
        return math.exp(-math.log(2) * days_since / half_life_days)
    except (ValueError, TypeError):
        return 1.0


def _get_briefing_half_life(db: sqlite3.Connection) -> float:
    """Return the configured recency half-life in days.

    Reads ``briefing_recency_half_life`` from the ``wakeup_config`` table.
    Falls back to 30.0 days when the key is absent or the table does not exist.
    """
    try:
        row = db.execute("SELECT value FROM wakeup_config WHERE key='briefing_recency_half_life'").fetchone()
        if row and row[0]:
            v = float(row[0])
            return v if v > 0 else 30.0
    except Exception:
        pass
    return 30.0


def _get_superseded_ids(db: sqlite3.Connection) -> set:
    """Return the set of knowledge_entries.id values that have been superseded.

    An entry is superseded when it appears as the *target* of a SUPERSEDES relation.
    Fail-open: returns empty set if the table or column is absent.
    """
    try:
        rows = db.execute(
            "SELECT target_id FROM knowledge_relations WHERE relation_type = 'SUPERSEDES' AND target_id IS NOT NULL"
        ).fetchall()
        return {int(r[0]) for r in rows}
    except Exception:
        return set()


def _expand_query_with_entities(db: sqlite3.Connection, task: str) -> list[str]:
    """Extract entities from task text and return matching knowledge_entry IDs.

    Used to boost entries that share entities with the current task.
    Returns empty list if knowledge_entities table doesn't exist.
    """
    import re as _re

    row = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_entities'").fetchone()
    if not row:
        return []

    file_re = _re.compile(r"\b[\w/.-]+\.(?:py|ts|js|go|rs|java|rb|cpp|h|json|yaml|yml|toml|md)\b")
    func_re = _re.compile(r"\b(?:def|function|fn|func|async\s+fn|async\s+function)\s+(\w+)")
    error_re = _re.compile(r"\b([A-Z][a-zA-Z]*(?:Error|Exception|Warning|Failure|Fault))\b")
    tool_re = _re.compile(r"`(sk|git|gh|npm|pip|cargo|ruff|pytest|python3?)\s+[\w-]+`")
    symbol_re = _re.compile(r"\b([A-Z][a-zA-Z0-9]{3,}(?:[A-Z][a-z]+)+)\b")

    entities: list[tuple[str, str]] = []
    for m in file_re.findall(task):
        if len(m) > 4:
            entities.append(("file_path", m.lower()))
    for m in func_re.findall(task):
        if len(m) > 2:
            entities.append(("function", m.lower()))
    for m in error_re.findall(task):
        entities.append(("error_type", m))
    for m in tool_re.findall(task):
        entities.append(("tool", m.lower()))
    for m in symbol_re.findall(task):
        if len(m) > 5:
            entities.append(("symbol", m))

    if not entities:
        return []

    placeholders = ",".join("(?,?)" for _ in entities)
    params = [v for pair in entities for v in pair]
    try:
        rows = db.execute(
            f"SELECT DISTINCT entry_id FROM knowledge_entities WHERE (entity_type, entity_value) IN ({placeholders})",
            params,
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [str(r[0]) for r in rows]


def _recency_composite_score(entry: dict, half_life_days: float) -> float:
    """Composite ranking score = priority_base + intensity * recency_decay.

    Priority is the strict outer dimension (issue #121): P0 entries always rank
    above P1, which always ranks above P2, etc., regardless of age or intensity.

    Additive formula guarantees strict cross-tier ordering:
      score = priority_base + (intensity * recency_decay)
    Priority bases: P0=4.0, P1=2.0, P2=0.0, P3=-2.0.
    The tier gap (2.0) exceeds the maximum within-tier contribution (intensity *
    decay ≤ 1.0 * 1.0 = 1.0), so P(n) always outscores P(n+1) regardless of
    intensity or age: P0 min (4.0) > P1 max (3.0) > P2 max (1.0) > P3 max (-1.0).

    Within the same priority bucket, the #88 intensity-first contract is preserved.
    Recency decay further boosts genuinely fresh entries over equally-intense stale ones.
    When intensity is absent (pre-v21 DB rows), falls back to the entry's confidence.
    When priority is absent (pre-v22 DB rows), defaults to P2 base (0.0).
    """
    # Priority base: P0=4.0, P1=2.0, P2=0.0, P3=-2.0.
    # Tier gap of 2.0 exceeds max(intensity * decay) = 1.0, guaranteeing strict ordering.
    priority_bases = {"P0": 4.0, "P1": 2.0, "P2": 0.0, "P3": -2.0}
    priority_raw = entry.get("priority")
    priority_base = priority_bases.get(priority_raw or "P2", 0.0)

    intensity_raw = entry.get("intensity")
    if intensity_raw is not None:
        intensity = float(intensity_raw)
    else:
        # Pre-v21 rows have no intensity column; use confidence to preserve
        # the existing confidence-based ordering instead of collapsing all
        # no-intensity rows to the same constant.
        confidence_raw = entry.get("confidence")
        intensity = float(confidence_raw) if confidence_raw is not None else 0.5
    decay = _recency_decay(entry.get("last_seen"), half_life_days)
    access_decay = _decay_weight(entry.get("last_accessed_at", ""))
    return priority_base + intensity * decay * access_decay


# ---------------------------------------------------------------------------
# Multi-signal RRF fusion helpers (issue #796)
# ---------------------------------------------------------------------------


def _rrf_fuse(ranked_lists: list[list[int]], k: int = 60) -> list[int]:
    """Reciprocal Rank Fusion across multiple ranked lists of entry IDs.
    Returns merged ranked list of entry IDs."""
    scores: dict[int, float] = {}
    for ranked in ranked_lists:
        for rank, entry_id in enumerate(ranked):
            scores[entry_id] = scores.get(entry_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores, key=lambda x: scores[x], reverse=True)


def _rank_by_bm25(entries: list[dict]) -> list[int]:
    """Return entry IDs ranked by existing bm25_score field (or 0 if absent)."""
    return [e["id"] for e in sorted(entries, key=lambda x: x.get("bm25_score", 0), reverse=True)]


def _rank_by_decay(entries: list[dict]) -> list[int]:
    """Rank entries by decay-adjusted confidence."""
    return [e["id"] for e in sorted(entries, key=lambda x: x.get("decay_score", x.get("confidence", 0)), reverse=True)]


def _rank_by_recall_freq(entries: list[dict]) -> list[int]:
    """Rank entries by recall frequency (recall_count / max(recall_days, 1))."""

    def _freq(e: dict) -> float:
        rc = e.get("recall_count", 0) or 0
        rd = e.get("recall_days", 1) or 1
        return rc / rd

    return [e["id"] for e in sorted(entries, key=_freq, reverse=True)]


def _apply_rrf_ranking(entries: list[dict]) -> list[dict]:
    """Re-rank entries using RRF fusion of BM25, decay, and recall frequency."""
    if len(entries) <= 1:
        return entries

    bm25_ranked = _rank_by_bm25(entries)
    decay_ranked = _rank_by_decay(entries)
    recall_ranked = _rank_by_recall_freq(entries)

    ranked_lists = [bm25_ranked, decay_ranked, recall_ranked]
    fused_ids = _rrf_fuse(ranked_lists)

    id_to_entry = {e["id"]: e for e in entries}
    return [id_to_entry[eid] for eid in fused_ids if eid in id_to_entry]


def search_knowledge_entries(
    db: sqlite3.Connection,
    query: str,
    category: str,
    limit: int = 3,
    min_confidence: float = 0.0,
    since_date: "str | None" = None,
    include_resolved: bool = False,
    exclude_ids: "set[int] | None" = None,
) -> list[dict]:
    """Search knowledge entries by category using FTS5 with adaptive strictness.

    ``since_date`` is an optional ISO-8601 date string (``YYYY-MM-DD``).  When
    provided, only entries whose ``last_seen >= since_date`` are returned.

    ``include_resolved``: when False (default), entries with ``is_resolved=1``
    are excluded so resolved mistakes don't clutter routine briefings.
    Pass ``include_resolved=True`` to surface all entries regardless of status.
    """
    fts_query, strictness, confidence_delta = _build_adaptive_fts_query(query)
    effective_confidence = max(0.0, min(1.0, min_confidence + confidence_delta))

    has_intensity = _ke_has_intensity(db)
    has_priority = _ke_has_priority(db)
    has_recurrence = _ke_has_recurrence(db)
    has_is_resolved = _ke_has_is_resolved(db)
    has_last_accessed = _ke_has_last_accessed(db)
    order_by = _intensity_order_expr("ke", has_priority) if has_intensity else "ke.confidence DESC, rank"
    # Extra columns fetched so Python-level recency composite scoring has priority + intensity + age.
    _rec_cols = ", COALESCE(ke.intensity, 0.5) as intensity, ke.last_seen" if has_intensity else ", ke.last_seen"
    _priority_col = ", COALESCE(ke.priority, 'P2') as priority" if has_priority else ""
    _recurrence_col = (
        ", COALESCE(ke.recurrence_after_briefing, 0) AS recurrence_after_briefing" if has_recurrence else ""
    )
    _last_accessed_col = ", ke.last_accessed_at" if has_last_accessed else ""

    # Build optional date-filter clause and params
    _date_clause = " AND ke.last_seen >= ?" if since_date else ""
    _date_params: list = [since_date] if since_date else []

    # Resolved filter: exclude is_resolved=1 entries unless caller opts in.
    _resolved_clause = (
        "" if include_resolved or not has_is_resolved else " AND (ke.is_resolved IS NULL OR ke.is_resolved = 0)"
    )

    # Session-scoped dedup: exclude already-served entry IDs (issue #783 --no-repeat).
    _exclude_clause = f" AND ke.id NOT IN ({','.join(str(i) for i in exclude_ids) or 'NULL'})" if exclude_ids else ""

    results = []
    try:
        rows = db.execute(
            f"""
            SELECT ke.id, ke.title, ke.content, ke.tags,
                   ke.confidence, ke.session_id, ke.occurrence_count,
                   ke.document_id, ke.source_section,
                   ke.source_file, ke.start_line, ke.end_line,
                   ke.code_language, ke.code_snippet,
                   d.doc_type as source_doc_type,
                   d.title as source_doc_title,
                   d.file_path as source_doc_file_path,
                   d.seq as source_doc_seq{_rec_cols}{_priority_col}{_recurrence_col}{_last_accessed_col}
            FROM ke_fts fts
            JOIN knowledge_entries ke ON fts.rowid = ke.id
            LEFT JOIN documents d ON ke.document_id = d.id
            WHERE ke_fts MATCH ?
            AND ke.category = ?
            AND ke.confidence >= ?{_date_clause}{_resolved_clause}{_exclude_clause}
            ORDER BY {order_by}
            LIMIT ?
        """,
            (fts_query, category, effective_confidence, *_date_params, limit),
        ).fetchall()
        results.extend([dict(r) for r in rows])
    except sqlite3.OperationalError:
        try:
            rows = db.execute(
                f"""
                SELECT ke.id, ke.title, ke.content, ke.tags,
                       ke.confidence, ke.session_id, ke.occurrence_count{_rec_cols}{_priority_col}{_recurrence_col}{_last_accessed_col}
                FROM ke_fts fts
                JOIN knowledge_entries ke ON fts.rowid = ke.id
                WHERE ke_fts MATCH ?
                AND ke.category = ?
                AND ke.confidence >= ?{_date_clause}{_resolved_clause}{_exclude_clause}
                ORDER BY {order_by}
                LIMIT ?
            """,
                (fts_query, category, effective_confidence, *_date_params, limit),
            ).fetchall()
            results.extend([dict(r) for r in rows])
        except sqlite3.OperationalError:
            pass

    # Strict fallback: if exact-match returned nothing, retry with prefix query
    if not results and strictness == "strict":
        base_query = _sanitize_fts_query(query)
        try:
            rows = db.execute(
                f"""
                SELECT ke.id, ke.title, ke.content, ke.tags,
                       ke.confidence, ke.session_id, ke.occurrence_count,
                       ke.document_id, ke.source_section,
                       ke.source_file, ke.start_line, ke.end_line,
                       ke.code_language, ke.code_snippet,
                       d.doc_type as source_doc_type,
                       d.title as source_doc_title,
                       d.file_path as source_doc_file_path,
                       d.seq as source_doc_seq{_rec_cols}{_priority_col}{_recurrence_col}{_last_accessed_col}
                FROM ke_fts fts
                JOIN knowledge_entries ke ON fts.rowid = ke.id
                LEFT JOIN documents d ON ke.document_id = d.id
                WHERE ke_fts MATCH ?
                AND ke.category = ?
                AND ke.confidence >= ?{_date_clause}{_resolved_clause}{_exclude_clause}
                ORDER BY {order_by}
                LIMIT ?
            """,
                (base_query, category, min_confidence, *_date_params, limit),
            ).fetchall()
            results.extend([dict(r) for r in rows])
        except sqlite3.OperationalError:
            try:
                rows = db.execute(
                    f"""
                    SELECT ke.id, ke.title, ke.content, ke.tags,
                           ke.confidence, ke.session_id, ke.occurrence_count{_rec_cols}{_priority_col}{_recurrence_col}{_last_accessed_col}
                    FROM ke_fts fts
                    JOIN knowledge_entries ke ON fts.rowid = ke.id
                    WHERE ke_fts MATCH ?
                    AND ke.category = ?
                   AND ke.confidence >= ?{_date_clause}{_resolved_clause}{_exclude_clause}
                    ORDER BY {order_by}
                    LIMIT ?
                """,
                    (base_query, category, min_confidence, *_date_params, limit),
                ).fetchall()
                results.extend([dict(r) for r in rows])
            except sqlite3.OperationalError:
                pass

    return results


def search_semantic(
    db: sqlite3.Connection,
    query: str,
    category: str,
    limit: int = 3,
    min_confidence: float = 0.0,
    include_resolved: bool = False,
) -> list[dict]:
    """Search knowledge entries using vector embeddings.

    ``include_resolved``: when False (default), post-filters out is_resolved=1
    entries so resolved mistakes don't surface in routine briefings.
    """
    try:
        sys.path.insert(0, str(TOOLS_DIR))
        from embed import (
            call_embedding_api,
            ensure_embedding_tables,
            load_config,
            resolve_provider,
            search_tfidf,
            vector_search,
        )

        config = load_config()
        ensure_embedding_tables(db)

        # Try API embedding
        provider_name, provider_config = resolve_provider(config)
        query_vector = None

        if provider_name and provider_config:
            try:
                vecs = call_embedding_api([query], provider_config)
                query_vector = vecs[0]
            except Exception as e:
                print(f"⚠ Embedding API call failed: {e}", file=sys.stderr)

        if query_vector:
            vec_results = vector_search(db, query_vector, source_type="knowledge", limit=limit * 3)
            results = []
            # Fetch intensity and priority so _recency_composite_score honours the
            # #88 intensity-first and #121 priority-outer contracts for semantic hits.
            # Guard against pre-v21 / pre-v22 DBs that lack these columns.
            _has_intensity = _ke_has_intensity(db)
            _has_priority = _ke_has_priority(db)
            _intensity_col = ", COALESCE(intensity, 0.5) as intensity" if _has_intensity else ""
            _priority_col = ", COALESCE(priority, 'P2') as priority" if _has_priority else ""
            for st, sid, score in vec_results:
                if score < 0.3:
                    continue
                row = db.execute(
                    f"""
                    SELECT id, title, content, tags, confidence,
                           session_id, occurrence_count, category, last_seen{_intensity_col}{_priority_col}
                    FROM knowledge_entries WHERE id = ? AND category = ?
                    AND confidence >= ?
                """,
                    (sid, category, min_confidence),
                ).fetchone()
                if row:
                    d = dict(row)
                    d["_semantic_score"] = float(score)
                    results.append(d)
            reranked = _apply_feedback_bias_to_knowledge(db, query, results)
            return reranked[:limit]

        # TF-IDF fallback
        if config.get("fallback") == "tfidf":
            row = db.execute("SELECT model_blob FROM tfidf_model WHERE id = 1").fetchone()
            if row and row[0]:
                tfidf_results = search_tfidf(query, row[0], limit=limit * 3)
                results = []
                seen_ids = set()
                for section_id, score in tfidf_results:
                    if score < 0.05:
                        continue
                    # Map TF-IDF section match to knowledge entries.
                    # Precision improvement (issue #376): prefer same-document entries
                    # (ke.document_id matches the section's document) before falling back
                    # to same-session entries.  This avoids retrieving unrelated entries
                    # from large sessions that happen to contain the matching section.
                    ke_rows = db.execute(
                        """
                        SELECT ke.* FROM knowledge_entries ke
                        WHERE ke.category = ?
                          AND (
                              ke.document_id = (
                                  SELECT document_id FROM sections WHERE id = ?
                              )
                              OR ke.session_id IN (
                                  SELECT d.session_id FROM sections s
                                  JOIN documents d ON s.document_id = d.id
                                  WHERE s.id = ?
                              )
                          )
                        ORDER BY
                            CASE WHEN ke.document_id = (
                                SELECT document_id FROM sections WHERE id = ?
                            ) THEN 0 ELSE 1 END,
                            ke.confidence DESC
                        LIMIT ?
                    """,
                        (category, section_id, section_id, section_id, limit),
                    ).fetchall()
                    if not ke_rows:
                        # Fallback: get top entries by confidence for this category
                        ke_rows = db.execute(
                            """
                            SELECT ke.* FROM knowledge_entries ke
                            WHERE ke.category = ?
                            ORDER BY ke.confidence DESC
                            LIMIT ?
                        """,
                            (category, limit),
                        ).fetchall()
                    for r in ke_rows:
                        d = dict(r)
                        eid = d.get("id")
                        if eid in seen_ids:
                            continue
                        seen_ids.add(eid)
                        d["_semantic_score"] = float(score)
                        results.append(d)
                reranked = _apply_feedback_bias_to_knowledge(db, query, results)
                return reranked[:limit]

    except ImportError:
        pass  # embed.py or scikit-learn not available
    except sqlite3.OperationalError:
        pass  # embedding tables don't exist yet

    return []


def _filter_resolved(results: list[dict], include_resolved: bool) -> list[dict]:
    """Post-filter: remove is_resolved=1 entries unless include_resolved is True."""
    if include_resolved:
        return results
    return [e for e in results if not e.get("is_resolved")]


def search_past_work(db: sqlite3.Connection, query: str, limit: int = 3) -> list[dict]:
    """Search past work/checkpoints related to query."""
    fts_query = _sanitize_fts_query(query.strip())

    results = []
    try:
        rows = db.execute(
            """
            SELECT fts.title, fts.doc_type, fts.session_id,
                   snippet(knowledge_fts, 2, '', '', '...', 40) as excerpt
            FROM knowledge_fts fts
            WHERE knowledge_fts MATCH ?
            AND fts.doc_type IN ('checkpoint', 'research')
            ORDER BY rank
            LIMIT ?
        """,
            (fts_query, limit),
        ).fetchall()
        results = [dict(r) for r in rows]
    except sqlite3.OperationalError:
        pass

    return results


def load_codebase_map_files() -> set:
    """Return the set of git-tracked file paths from the most recent codebase-map.md.

    Reads the artifact produced by codebase-map.py from the most recently
    modified Copilot session files/ directory.  Returns an empty set on any
    failure so callers degrade gracefully when the artifact is absent.
    """
    import re as _re

    try:
        if not SESSION_STATE.exists():
            return set()
        sessions = sorted(
            (d for d in SESSION_STATE.iterdir() if d.is_dir()),
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        for session_dir in sessions[:3]:
            map_path = session_dir / "files" / "codebase-map.md"
            if map_path.exists():
                content = map_path.read_text(encoding="utf-8", errors="replace")
                files = set()
                for line in content.splitlines():
                    m = _re.match(r"^\s*-\s+`([^`]+)`\s*$", line)
                    if m:
                        files.add(m.group(1))
                if files:
                    return files
    except Exception:
        pass
    return set()


def _current_repo_root() -> str:
    """Return the git repo root of the current working directory.

    Fail-open: returns '' when git is unavailable, cwd is not a repo, or any
    error occurs.  The caller (generate_briefing) passes this value as the
    *repo_root* filter to query_file_annotations so that multi-repo knowledge
    bases don't cross-contaminate each other's file annotations.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            raw = result.stdout.strip()
            return Path(raw).resolve().as_posix()
    except Exception:
        pass
    return ""


def query_file_annotations(
    db: sqlite3.Connection,
    query: str = "",
    repo_root: str = "",
    limit: int = 8,
) -> list[dict]:
    """Return relevant file annotations from the file_annotations table.

    Fails open: returns [] when the table does not exist or any error occurs.
    When *query* is provided, matches file_path or description by substring.
    When *repo_root* is provided, filters to that repository root.
    """
    try:
        rows = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='file_annotations'").fetchone()
        if not rows:
            return []

        params: list = []
        conditions: list[str] = []

        if repo_root:
            conditions.append("repo_root = ?")
            params.append(repo_root)

        if query:
            conditions.append("(file_path LIKE ? OR description LIKE ?)")
            like = f"%{query[:100]}%"
            params.extend([like, like])

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)

        db_rows = db.execute(
            f"""
            SELECT file_path, description, est_tokens
            FROM file_annotations
            {where}
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [{"file_path": r[0], "description": r[1], "est_tokens": r[2]} for r in db_rows]
    except Exception:
        return []  # always fail-open


def _format_file_annotations_block(annotations: list[dict], max_entries: int = 6) -> str:
    """Render a compact file-annotations block for briefing output.

    Returns an empty string when annotations is empty.
    """
    if not annotations:
        return ""
    lines = ["🗂 File Index (top files)"]
    for ann in annotations[:max_entries]:
        fp = ann.get("file_path", "")
        desc = ann.get("description", "")
        tok = ann.get("est_tokens", 0)
        tok_str = f" ~{tok}tok" if tok else ""
        lines.append(f"  `{fp}`{tok_str} — {desc}")
    return "\n".join(lines)


def blast_radius(db: sqlite3.Connection, query: str) -> list[dict]:
    """Analyze blast radius: find files mentioned in task and their risk from knowledge DB.

    Returns list of dicts:
        {file, mistakes, patterns, decisions, risk_level, risk_emoji, stale}

    The ``stale`` flag is True when the file pattern looks like a real path but
    does not appear in the current codebase-map.md tracked-file inventory.
    Stale entries are shown last within the same risk tier.
    """
    import re

    # Extract file paths from the query (e.g., "fix src/auth.py and models/user.py")
    file_patterns = re.findall(
        r"(?:^|\s)((?:[\w.-]+/)*[\w.-]+\.(?:py|js|ts|jsx|tsx|kt|java|swift|rb|go|rs|sh|json|yaml|yml|toml|md|sql|css|html))\b",
        query,
    )

    if not file_patterns:
        # Try extracting module/feature names for broader matching
        words = [
            w
            for w in query.split()
            if len(w) > 3
            and w.lower()
            not in (
                "implement",
                "create",
                "update",
                "modify",
                "refactor",
                "review",
                "check",
                "build",
                "that",
                "this",
                "with",
                "from",
                "have",
            )
        ]
        if not words:
            return []
        file_patterns = words[:5]

    results = []
    for pattern in file_patterns:
        # Search knowledge entries mentioning this file/module
        safe_pattern = pattern.replace("'", "''")
        counts = {"mistake": 0, "pattern": 0, "decision": 0}

        for category in counts:
            try:
                row = db.execute(
                    """
                    SELECT COUNT(*) FROM knowledge_entries
                    WHERE category = ?
                    AND (content LIKE ? OR title LIKE ?)
                """,
                    (category, f"%{safe_pattern}%", f"%{safe_pattern}%"),
                ).fetchone()
                if row:
                    counts[category] = row[0]
            except sqlite3.OperationalError:
                pass

        total = counts["mistake"] + counts["pattern"] + counts["decision"]
        if total == 0:
            continue

        if counts["mistake"] >= 3:
            risk_level, risk_emoji = "HIGH", "🔴"
        elif counts["mistake"] >= 1:
            risk_level, risk_emoji = "MEDIUM", "🟡"
        else:
            risk_level, risk_emoji = "LOW", "🟢"

        results.append(
            {
                "file": pattern,
                "mistakes": counts["mistake"],
                "patterns": counts["pattern"],
                "decisions": counts["decision"],
                "risk_level": risk_level,
                "risk_emoji": risk_emoji,
            }
        )

    # Cross-reference with codebase-map.md to mark stale blast-radius entries.
    # An entry is "stale" when the pattern looks like a real file path (has an
    # extension) but does not appear in the current tracked-file inventory.
    tracked = load_codebase_map_files()
    for r in results:
        fname = r["file"]
        if tracked and "." in Path(fname).name:
            r["stale"] = fname not in tracked and not any(f.endswith("/" + fname) for f in tracked)
        else:
            r["stale"] = False  # keywords or no map available → no stale flag

    # Sort: risk tier first (HIGH → MEDIUM → LOW); stale entries last within tier
    risk_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    results.sort(key=lambda x: (risk_order.get(x["risk_level"], 3), 1 if x.get("stale") else 0))
    return results


def generate_subagent_context(
    query: str, limit: int = 3, min_confidence: float = 0.5, mode: str = "auto", infer_auto_mode: bool = True
) -> str:
    """Generate compact context block for injecting into sub-agent prompts.

    Output is ~200-400 tokens — minimal overhead for sub-agent context windows.
    Format: plain text with no markdown formatting for easy prompt embedding.
    """
    db = get_db()
    lines = ["[KNOWLEDGE CONTEXT — from past sessions]"]
    rewritten_query = _rewrite_query_local(query)
    _, categories, per_cat_limit = _mode_category_config(limit, mode, query, infer_auto=infer_auto_mode)
    labels = {"mistake": "AVOID", "pattern": "USE", "decision": "NOTE", "tool": "CONFIG"}
    half_life = _get_briefing_half_life(db)
    clarify_entry = _load_matching_clarification(query, repo_root=_current_repo_root())

    if clarify_entry:
        lines.append(f"  [CLARIFY] {_word_trim(clarify_entry.get('clarified_task', ''), 140)}")
        for question in clarify_entry.get("questions", [])[:3]:
            category = question.get("category", "?")
            prompt = _word_trim(question.get("question", ""), 100)
            lines.append(f"  [QUESTION] {category}: {prompt}")

    for cat in categories:
        label = labels.get(cat, cat.upper())
        cat_limit = per_cat_limit.get(cat, limit)
        # Overfetch FTS so reranking has a wider candidate pool; truncation
        # happens after priority-aware rerank, not before (issue #121 Blocker 3).
        fetch_limit = max(cat_limit * 2, cat_limit + 6)
        fts = search_knowledge_entries(db, rewritten_query, cat, fetch_limit, min_confidence=min_confidence)
        # Widen semantic fetch to match FTS so outer priority-aware rerank sees the
        # full candidate pool before truncation (issue #121 Blocker 4).
        # Issue #369: use rewritten_query consistently so semantic path benefits from
        # query condensation like the FTS path does.
        sem = search_semantic(db, rewritten_query, cat, fetch_limit, min_confidence=min_confidence)
        # Merge and dedup by id
        seen = set()
        entries = []
        for e in fts + sem:
            eid = e[0] if isinstance(e, (list, tuple)) else e.get("id", id(e))
            if eid not in seen:
                seen.add(eid)
                entries.append(e)

        # Rerank by composite recency+priority score so high-priority semantic
        # hits are not dropped behind lower-priority FTS hits (issue #121 Blocker 3).
        entries.sort(key=lambda e: _recency_composite_score(e, half_life), reverse=True)

        for e in entries[:cat_limit]:
            if isinstance(e, (list, tuple)):
                title = e[1] if len(e) > 1 else str(e)
            else:
                title = e.get("title", str(e))
            # Truncate title for compactness
            title_short = str(title)[:120]
            lines.append(f"  [{label}] {title_short}")

    if len(lines) == 1:
        return ""  # No relevant context found

    lines.append("[END KNOWLEDGE CONTEXT]")
    return "\n".join(lines)


def _collect_skill_usage_for_briefing(db_path: Path = None) -> list:
    """Read event-level skill usage from skill-metrics.db.

    Returns a list of dicts, one per skill, with triggered/loaded/skipped counts.
    Returns an empty list when the DB or table is absent (fail-open).
    """
    if db_path is None:
        db_path = Path.home() / ".copilot" / "session-state" / "skill-metrics.db"
    try:
        if not db_path.exists():
            return []
        db = sqlite3.connect(str(db_path))
        db.row_factory = sqlite3.Row
        try:
            row = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='skill_usage_events'").fetchone()
            if not row:
                return []
            rows = db.execute(
                "SELECT skill_name, "
                "SUM(CASE WHEN event='triggered' THEN 1 ELSE 0 END) AS triggered, "
                "SUM(CASE WHEN event='loaded'    THEN 1 ELSE 0 END) AS loaded, "
                "SUM(CASE WHEN event='skipped'   THEN 1 ELSE 0 END) AS skipped, "
                "CASE WHEN SUM(CASE WHEN event='triggered' THEN 1 ELSE 0 END) > 0 "
                "     THEN CAST(SUM(CASE WHEN event='loaded' THEN 1 ELSE 0 END) AS REAL) "
                "          / SUM(CASE WHEN event='triggered' THEN 1 ELSE 0 END) "
                "     ELSE 0.0 END AS load_rate "
                "FROM skill_usage_events "
                "GROUP BY skill_name "
                "ORDER BY load_rate DESC, triggered DESC"
            ).fetchall()
        finally:
            db.close()
        by_skill: dict = {}
        for r in rows:
            skill = r["skill_name"]
            by_skill[skill] = {
                "skill_name": skill,
                "triggered": int(r["triggered"]),
                "loaded": int(r["loaded"]),
                "skipped": int(r["skipped"]),
                "load_rate": float(r["load_rate"]),
            }
        return list(by_skill.values())
    except Exception:
        return []


def _format_skill_usage_section(entries: list) -> str:
    """Return a compact skill usage section for briefing output.

    Shows top skills (highest load rate) and, when available, highlights the
    bottom skills (lowest load rate) so the operator can spot underperforming
    skills.  ``entries`` must be pre-sorted by ``load_rate DESC`` (as returned
    by ``_collect_skill_usage_for_briefing``).

    Returns an empty string when entries is empty.
    """
    if not entries:
        return ""
    lines = ["📦 Skill Usage (top/bottom by load rate)"]
    top = entries[:4]
    # Bottom-2: only include when there are enough distinct skills to avoid
    # duplicating entries already shown in the top block.
    bottom = [e for e in entries[4:][-2:]] if len(entries) > 4 else []
    for entry in top:
        name = entry.get("skill_name", "?")
        triggered = entry.get("triggered", 0)
        loaded = entry.get("loaded", 0)
        load_rate = entry.get("load_rate", 0.0)
        rate_pct = f"{load_rate:.0%}"
        lines.append(f"  {name:<30} {rate_pct:>5} load  ({loaded}/{triggered} triggered)")
    if bottom:
        lines.append("  ↓ lowest load rate:")
        for entry in bottom:
            name = entry.get("skill_name", "?")
            triggered = entry.get("triggered", 0)
            loaded = entry.get("loaded", 0)
            load_rate = entry.get("load_rate", 0.0)
            rate_pct = f"{load_rate:.0%}"
            lines.append(f"  {name:<30} {rate_pct:>5} load  ({loaded}/{triggered} triggered)")
    return "\n".join(lines)


def generate_briefing(
    query: str,
    limit: int = 3,
    fmt: str = "md",
    full: bool = False,
    min_confidence: float = 0.5,
    mode: str = "auto",
    infer_auto_mode: bool = True,
    with_meta: bool = False,
    include_superseded: bool = False,
    since_date: "str | None" = None,
    include_resolved: bool = False,
    pinned_n: int = 0,
    exclude_ids: "set[int] | None" = None,
    no_dedup: bool = False,
):
    """Generate a structured briefing from the knowledge base.

    ``include_resolved``: when False (default), entries with ``is_resolved=1``
    are excluded from briefings so resolved mistakes don't clutter context.
    Pass ``--include-resolved`` on the CLI or ``include_resolved=True`` to
    show resolved entries alongside open ones.

    ``pinned_n``: when > 0, always prepend the top-N P0 priority entries to the
    briefing output regardless of the query (issue #708 --pinned flag).

    ``no_dedup``: when True, skip the semantic near-duplicate collapse pass
    (issue #851).  Dedup is applied by default in compact mode only.
    """
    db = get_db()
    rewritten_query = _rewrite_query_local(query)
    active_mode, categories, per_cat_limit = _mode_category_config(limit, mode, query, infer_auto=infer_auto_mode)
    half_life = _get_briefing_half_life(db)

    superseded_ids: set = set() if include_superseded else _get_superseded_ids(db)
    entity_matched_ids = set(_expand_query_with_entities(db, query))

    briefing_data = {}
    global_seen_titles = set()  # Cross-category dedup

    for cat in categories:
        # Combine FTS5 + semantic results, deduplicate.
        # Fetch a wider candidate pool so recency weighting can surface better
        # recent entries that would otherwise be hidden by the SQL LIMIT.
        cat_limit = per_cat_limit.get(cat, limit)
        fetch_limit = max(cat_limit * 2, cat_limit + 6)
        fts_results = search_knowledge_entries(
            db,
            rewritten_query,
            cat,
            fetch_limit,
            min_confidence=min_confidence,
            since_date=since_date,
            include_resolved=include_resolved,
            exclude_ids=exclude_ids,
        )
        # Widen semantic fetch symmetrically so the outer priority rerank has the same
        # wide candidate pool for semantic hits as it does for FTS hits (issue #121 Blocker 4).
        # Issue #369: use rewritten_query consistently for semantic search so FTS and
        # semantic paths operate on the same condensed terms.
        sem_results = search_semantic(
            db, rewritten_query, cat, fetch_limit, min_confidence=min_confidence, include_resolved=include_resolved
        )

        merged = []
        for r in fts_results + sem_results:
            title = r.get("title", "")
            if title not in global_seen_titles:
                global_seen_titles.add(title)
                # Post-filter semantic results by since_date (FTS already filtered in SQL)
                if since_date and r.get("last_seen") and str(r["last_seen"])[:10] < since_date:
                    continue
                merged.append(r)

        def _entity_bonus(e: dict) -> float:
            """Additive boost for entries sharing entities with the task (issue #770)."""
            return 0.2 if str(e.get("id", "")) in entity_matched_ids else 0.0

        # For mistakes, boost recurring entries to the top before composite recency sort.
        # Recurring mistakes (re-encountered after a briefing) are the most actionable signal.
        if cat == "mistake":
            merged.sort(
                key=lambda e: (
                    -(int(e.get("recurrence_after_briefing") or 0)),
                    -(_recency_composite_score(e, half_life) + _entity_bonus(e)),
                )
            )
        else:
            # Rerank by composite recency score before truncating so that a recent
            # entry can always surface ahead of an equally-intense stale one.
            merged.sort(
                key=lambda e: _recency_composite_score(e, half_life) + _entity_bonus(e),
                reverse=True,
            )

        # Issue #796: Apply multi-signal RRF fusion (BM25, decay, recall frequency).
        # For mistakes, preserve danger-lane (recurring) entries at the top, then apply
        # RRF to the remaining pool so the recurrence boost is never overridden.
        if cat == "mistake":
            danger = [e for e in merged if int(e.get("recurrence_after_briefing") or 0) > 0]
            rest = [e for e in merged if not int(e.get("recurrence_after_briefing") or 0)]
            merged = danger + _apply_rrf_ranking(rest)
        else:
            merged = _apply_rrf_ranking(merged)

        # WBS-014: defense-in-depth read-side credential/injection filter
        # Issue #377: universal status-note suppression — applied here so ALL
        # output formats (text, json, pack, compact) consistently omit Wave-style
        # status-note entries, not just the compact formatter.
        safe_entries = []
        for e in merged[:cat_limit]:
            if _briefing_entry_is_unsafe(e):
                print(
                    f"  [briefing] suppressed unsafe entry: {e.get('title', '')[:60]!r}",
                    file=sys.stderr,
                )
            elif _STATUS_NOTE_RE.search(e.get("title", "")):
                pass  # suppress Wave-style status/progress notes universally
            elif e.get("id") and int(e["id"]) in superseded_ids:
                pass  # suppress entries that have been superseded by a newer entry
            else:
                safe_entries.append(e)
        briefing_data[cat] = safe_entries

    # Issue #851: semantic near-duplicate collapse — compact mode only, not full/wakeup.
    # Applied after the per-category safety filter so dedup never bypasses suppression.
    # Emits a briefing_merge_event for telemetry when entries are actually collapsed.
    if not no_dedup and fmt == "compact":
        total_before = sum(len(v) for v in briefing_data.values())
        for cat in list(briefing_data.keys()):
            briefing_data[cat] = _dedup_entries(briefing_data[cat])
        total_after = sum(len(v) for v in briefing_data.values())
        merged_count = total_before - total_after
        if merged_count > 0:
            _emit_knowledge_event_fail_open(
                "briefing_merge_event",
                {"merged_count": merged_count, "query": query[:120], "fmt": fmt},
            )

    past_work = search_past_work(db, rewritten_query, limit)

    # Blast radius analysis
    blast = blast_radius(db, rewritten_query)

    # File annotations (fail-open when table absent; scoped to current repo)
    _repo_root = _current_repo_root()
    file_annotations = query_file_annotations(db, query=rewritten_query, repo_root=_repo_root, limit=6)
    constitution_entry = _load_constitution(_repo_root)
    clarify_entry = _load_matching_clarification(query, repo_root=_repo_root)

    # Pinned P0 entries — always included when pinned_n > 0 (issue #708 --pinned)
    pinned_entries: list[dict] = []
    if pinned_n > 0:
        pinned_entries = _fetch_pinned_p0_entries(db, limit=pinned_n)

    # Pack-only machine surface extras
    task_matches = []
    file_matches = []
    next_open = []
    if fmt == "pack":
        task_matches = _extract_task_matches(db, rewritten_query, limit=min(5, max(3, limit)))
        file_matches = _extract_file_matches(db, rewritten_query, limit=min(5, max(3, limit)))
        next_open = _extract_next_open(limit=5)

    # Record briefing deliveries for recurrence tracking
    try:
        session_id = _detect_session_id()
        if session_id:
            for entries in briefing_data.values():
                for entry in entries:
                    eid = entry.get("id")
                    if eid:
                        try:
                            db.execute(
                                "INSERT OR IGNORE INTO briefing_deliveries (session_id, entry_id) VALUES (?, ?)",
                                (session_id, eid),
                            )
                        except Exception:
                            pass
            db.commit()
    except Exception:
        pass  # fail-open: delivery tracking is non-critical

    # Per-entry recall stats — compute IDs while connection is still open.
    selected_entry_ids = _safe_int_list(
        row.get("id") for rows in briefing_data.values() for row in rows if isinstance(row, dict)
    )
    _upsert_entry_recall_stats(db, selected_entry_ids, rewritten_query)

    # Update access tracking for surfaced entries (Ebbinghaus decay — issue #769)
    try:
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        for entries in briefing_data.values():
            for entry in entries:
                eid = entry.get("id")
                if eid:
                    try:
                        db.execute(
                            "UPDATE knowledge_entries SET last_accessed_at=?, access_count=access_count+1 WHERE id=?",
                            (now_iso, eid),
                        )
                    except sqlite3.OperationalError:
                        pass  # Column not yet migrated — skip silently
        db.commit()
    except Exception:
        pass  # fail-open: access tracking is non-critical

    db.close()

    # Check if we have anything
    total_entries = sum(len(v) for v in briefing_data.values()) + len(past_work) + (1 if constitution_entry else 0)
    pinned_block = ""
    if pinned_entries:
        pinned_lines = ["## 📌 Pinned P0 Entries (always shown)"]
        for pe in pinned_entries:
            pinned_lines.append(f"- [{pe.get('id')}] **{pe.get('title', '')}** ({pe.get('category', '')})")
        pinned_block = "\n".join(pinned_lines) + "\n\n"
    output = ""
    if total_entries == 0 and not pinned_entries:
        if fmt == "json":
            output = json.dumps(
                {
                    "query": query,
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "sections": {},
                    "constitution": _serialize_constitution(constitution_entry),
                    "clarify": _serialize_clarification(clarify_entry),
                    "message": "No relevant past experience found.",
                },
                indent=2,
            )
        elif fmt == "pack":
            pack = {
                "query": query,
                "rewritten_query": rewritten_query,
                "mode": active_mode,
                "risk": [],
                "entries": {"mistake": [], "pattern": [], "decision": [], "tool": []},
                "task_matches": task_matches,
                "file_matches": file_matches,
                "past_work": [],
                "next_open": next_open,
                "constitution": _serialize_constitution(constitution_entry),
                "clarify": _serialize_clarification(clarify_entry),
            }
            output = json.dumps(pack, indent=2, ensure_ascii=False)
        else:
            if constitution_entry or clarify_entry:
                if fmt == "compact":
                    output = _format_compact(
                        query,
                        briefing_data,
                        past_work,
                        categories,
                        blast,
                        file_annotations,
                        constitution_entry,
                        clarify_entry,
                    )
                elif full:
                    output = _format_markdown(
                        query,
                        briefing_data,
                        past_work,
                        categories,
                        blast,
                        file_annotations,
                        constitution_entry,
                        clarify_entry,
                    )
                else:
                    output = _format_default(
                        query,
                        briefing_data,
                        past_work,
                        categories,
                        blast,
                        file_annotations,
                        constitution_entry,
                        clarify_entry,
                    )
            else:
                output = f"No relevant past experience found for: {query}\n"
    else:
        # Format output
        if fmt == "json":
            output = _format_json(query, briefing_data, past_work, categories, blast, constitution_entry, clarify_entry)
        elif fmt == "pack":
            pack_entries = {
                k: [_serialize_pack_entry(e) for e in briefing_data.get(k, [])]
                for k in ("mistake", "pattern", "decision", "tool")
            }
            pack = {
                "query": query,
                "rewritten_query": rewritten_query,
                "mode": active_mode,
                "risk": [
                    {
                        "file": b.get("file", ""),
                        "risk_level": b.get("risk_level", ""),
                        "mistakes": b.get("mistakes", 0),
                        "patterns": b.get("patterns", 0),
                        "decisions": b.get("decisions", 0),
                        "stale": bool(b.get("stale", False)),
                    }
                    for b in (blast or [])
                ],
                "entries": pack_entries,
                "task_matches": task_matches,
                "file_matches": file_matches,
                "past_work": [
                    {
                        "title": w.get("title", ""),
                        "type": w.get("doc_type", ""),
                        "session": w.get("session_id", "")[:8],
                        "excerpt": w.get("excerpt", "")[:200],
                    }
                    for w in past_work
                ],
                "next_open": next_open,
                "constitution": _serialize_constitution(constitution_entry),
                "clarify": _serialize_clarification(clarify_entry),
            }
            output = json.dumps(pack, indent=2, ensure_ascii=False)
        elif fmt == "compact":
            output = _format_compact(
                query, briefing_data, past_work, categories, blast, file_annotations, constitution_entry, clarify_entry
            )
        elif full:
            output = _format_markdown(
                query, briefing_data, past_work, categories, blast, file_annotations, constitution_entry, clarify_entry
            )
        else:
            output = _format_default(
                query, briefing_data, past_work, categories, blast, file_annotations, constitution_entry, clarify_entry
            )

    # Append event-level skill usage section (non-pack formats only; fail-open).
    if fmt not in ("json", "pack"):
        try:
            _skill_entries = _collect_skill_usage_for_briefing()
            _skill_section = _format_skill_usage_section(_skill_entries)
            if _skill_section:
                output = output + "\n\n" + _skill_section
        except Exception:
            pass

    # Prepend pinned P0 block when --pinned is active (non-JSON, non-pack only)
    if pinned_block and fmt not in ("json", "pack"):
        output = pinned_block + output

    if with_meta:
        return output, {
            "surface": "pack" if fmt == "pack" else "standard",
            "mode": active_mode,
            "raw_query": query,
            "rewritten_query": rewritten_query,
            "task_id": "",
            "selected_entry_ids": selected_entry_ids,
            "hit_count": len(selected_entry_ids),
            "output_chars": len(output),
        }
    return output


def _format_default(
    query: str,
    data: dict,
    past_work: list,
    categories: dict,
    blast: list = None,
    file_annotations: list | None = None,
    constitution_entry: dict | None = None,
    clarify_entry: dict | None = None,
) -> str:
    """Compact default format: titles + 1-line summaries (~500 tokens)."""
    lines = []
    lines.append(f"📋 Briefing: {query}")
    lines.append("")

    lines.extend(_format_constitution_default_block(constitution_entry))
    lines.extend(_format_clarification_default_block(clarify_entry))

    for cat, meta in categories.items():
        entries = data.get(cat, [])
        if not entries:
            continue

        lines.append(f"{meta['emoji']} {meta['title']}")
        for entry in entries:
            eid = entry.get("id", "?")
            title = entry.get("title", "Untitled")
            if len(title) > 80:
                title = title[:77] + "..."
            # Extract 1-line summary from content
            content = entry.get("content", "")
            summary = ""
            for ln in content.split("\n"):
                ln = ln.strip().lstrip("-").lstrip("*").lstrip("0123456789.").strip()
                if (
                    ln
                    and len(ln) > 15
                    and not ln.startswith("#")
                    and not ln.startswith("|")
                    and not ln.startswith(">")
                    and not ln.startswith("```")
                ):
                    summary = ln[:80]
                    break
            # Include error lifecycle metadata when available
            meta_parts = []
            sev = entry.get("severity", "")
            if sev and sev != "medium":
                sev_emoji = {"critical": "🔴", "high": "🟠", "low": "🟢"}.get(sev, "")
                meta_parts.append(f"{sev_emoji}{sev}")
            et = entry.get("error_type", "")
            if et:
                meta_parts.append(et)
            rc = entry.get("root_cause", "")
            if rc:
                meta_parts.append(f"cause: {rc[:60]}")
            meta_str = f" [{', '.join(meta_parts)}]" if meta_parts else ""
            if summary:
                lines.append(f"  #{eid} {title}{meta_str} — {summary}")
            else:
                lines.append(f"  #{eid} {title}{meta_str}")
        lines.append("")

    if blast:
        lines.append("💥 Blast Radius")
        for b in blast:
            parts = f"{b['mistakes']}m/{b['patterns']}p/{b['decisions']}d"
            stale_tag = " (stale)" if b.get("stale") else ""
            lines.append(f"  {b['risk_emoji']} {b['risk_level']}: {b['file']}{stale_tag} — {parts}")
        lines.append("")

    if past_work:
        lines.append("📚 Related Past Work")
        for w in past_work:
            sid = w.get("session_id", "?")[:8]
            title = w.get("title", "?")
            if len(title) > 80:
                title = title[:77] + "..."
            lines.append(f"  [{w.get('doc_type', '?')}] {title} (session {sid})")
        lines.append("")

    if file_annotations:
        fa_block = _format_file_annotations_block(file_annotations)
        if fa_block:
            lines.append(fa_block)
            lines.append("")

    total = sum(len(v) for v in data.values()) + len(past_work) + (1 if constitution_entry else 0)
    lines.append(
        f"({total} entries) Use --full for complete content, or query-session.py --detail <id> for specific entry"
    )

    return "\n".join(lines)


def _format_markdown(
    query: str,
    data: dict,
    past_work: list,
    categories: dict,
    blast: list = None,
    file_annotations: list | None = None,
    constitution_entry: dict | None = None,
    clarify_entry: dict | None = None,
) -> str:
    """Format briefing as Markdown."""
    lines = []
    lines.append("# 📋 Pre-Task Briefing")
    lines.append("")
    lines.append(f"**Task**: {query}")
    lines.append(f"**Generated**: {time.strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    lines.append("---")
    lines.append("")

    lines.extend(_format_constitution_markdown_block(constitution_entry))
    lines.extend(_format_clarification_markdown_block(clarify_entry))

    for cat, meta in categories.items():
        entries = data.get(cat, [])
        if not entries:
            continue

        lines.append(f"## {meta['emoji']} {meta['title']}")
        lines.append("")
        lines.append(f"_{meta['desc']}_")
        lines.append("")

        for i, entry in enumerate(entries, 1):
            title = entry.get("title", "Untitled")
            content = entry.get("content", "")
            tags = entry.get("tags", "")
            confidence = entry.get("confidence", 0)
            count = entry.get("occurrence_count", 1)

            lines.append(f"### {i}. {title}")
            if tags:
                lines.append(
                    f"Tags: `{tags}` | Confidence: {confidence:.1f}" + (f" | Seen {count}x" if count > 1 else "")
                )
            lines.append("")

            # Limit content preview
            preview = content[:500]
            if len(content) > 500:
                preview += "..."
            lines.append(preview)
            lines.append("")

        lines.append("")

    if past_work:
        lines.append("## 📚 Related Past Work")
        lines.append("")
        lines.append("_Previous sessions that worked on similar topics._")
        lines.append("")
        for i, work in enumerate(past_work, 1):
            sid = work.get("session_id", "?")[:8]
            lines.append(f"{i}. **{work.get('title', '?')}** ({work.get('doc_type', '?')}, session `{sid}..`)")
            excerpt = work.get("excerpt", "")[:200]
            if excerpt:
                lines.append(f"   {excerpt}")
            lines.append("")

    if blast:
        lines.append("## 💥 Blast Radius")
        lines.append("")
        lines.append("| File | Risk | Mistakes | Patterns | Decisions |")
        lines.append("|------|------|----------|----------|-----------|")
        for b in blast:
            file_label = f"`{b['file']}`" + (" *(stale)*" if b.get("stale") else "")
            lines.append(
                f"| {file_label} | {b['risk_emoji']} {b['risk_level']} "
                f"| {b['mistakes']} | {b['patterns']} | {b['decisions']} |"
            )
        lines.append("")

    if file_annotations:
        lines.append("## 📁 Relevant Files")
        lines.append("")
        for ann in file_annotations[:6]:
            fp = ann.get("file_path", "")
            desc = ann.get("description", "")
            tok = ann.get("est_tokens", 0)
            tok_str = f" ~{tok}tok" if tok else ""
            lines.append(f"- `{fp}`{tok_str}: {desc}")
        lines.append("")

    lines.append("---")
    lines.append(
        f"_Briefing from knowledge.db — {sum(len(v) for v in data.values()) + (1 if constitution_entry else 0)} entries + {len(past_work)} past work refs_"
    )

    return "\n".join(lines)


def _format_json(
    query: str,
    data: dict,
    past_work: list,
    categories: dict,
    blast: list = None,
    constitution_entry: dict | None = None,
    clarify_entry: dict | None = None,
) -> str:
    """Format briefing as JSON."""
    output = {"query": query, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "sections": {}}

    constitution = _serialize_constitution(constitution_entry)
    if constitution:
        output["sections"]["constitution"] = {
            "title": "Constitution",
            "entry": constitution,
        }

    clarify = _serialize_clarification(clarify_entry)
    if clarify:
        output["sections"]["clarify"] = {
            "title": "Clarify Gate",
            "entry": clarify,
        }

    for cat, meta in categories.items():
        entries = data.get(cat, [])
        if entries:
            output["sections"][cat] = {
                "title": meta["title"],
                "entries": [
                    {
                        "title": e.get("title", ""),
                        "content": e.get("content", "")[:500],
                        "tags": e.get("tags", ""),
                        "confidence": e.get("confidence", 0),
                    }
                    for e in entries
                ],
            }

    if past_work:
        output["sections"]["past_work"] = {
            "title": "Related Past Work",
            "entries": [
                {
                    "title": w.get("title", ""),
                    "type": w.get("doc_type", ""),
                    "session": w.get("session_id", "")[:8],
                    "excerpt": w.get("excerpt", "")[:200],
                }
                for w in past_work
            ],
        }

    if blast:
        output["blast_radius"] = blast

    return json.dumps(output, indent=2, ensure_ascii=False)


def _word_trim(s: str, limit: int = 80) -> str:
    """Trim *s* to at most *limit* chars.

    When the string is already at *limit* (i.e. stored-truncated) and the
    trailing fragment looks like an incomplete word (≤ 3 alpha chars after the
    last space), strips that fragment so the output does not expose raw suffixes
    like ``tou`` (from ``touched``). This is a narrow heuristic, not a full
    word-boundary reflow.

    The threshold is deliberately ≤ 3 (not 4) to avoid false-positive stripping
    of legitimate 4-char terminal words such as "null", "stop", "hang", "call",
    "from", etc., which can naturally appear at position 80 in a complete title.
    """
    if len(s) > limit:
        s = s[:limit]
    if len(s) == limit:
        idx = s.rfind(" ")
        if idx != -1:
            last_word = s[idx + 1 :]
            if last_word.isalpha() and len(last_word) <= 3:
                return s[:idx].rstrip()
    return s


def _format_clarification_default_block(entry: dict | None) -> list[str]:
    """Render a plain-text clarification block for briefing output."""
    clarify = _serialize_clarification(entry)
    if not clarify:
        return []
    lines = ["❓ Clarify Gate"]
    task = _word_trim(clarify.get("clarified_task", ""), 180)
    if task:
        lines.append(f"  Ready brief: {task}")
    for index, question in enumerate(clarify.get("questions", [])[:5], 1):
        q_text = _word_trim(question.get("question", ""), 120)
        category = question.get("category", "?")
        lines.append(f"  {index}. [{category}] {q_text}")
        options = " | ".join(question.get("options", [])[:4])
        if options:
            lines.append(f"     {options}")
    lines.append("")
    return lines


def _format_constitution_default_block(entry: dict | None) -> list[str]:
    constitution = _serialize_constitution(entry)
    if not constitution:
        return []
    lines = ["🏛️ Constitution"]
    version = constitution.get("version", "")
    amended = constitution.get("last_amended", "")
    meta = " | ".join(part for part in [f"Version {version}" if version else "", amended] if part)
    if meta:
        lines.append(f"  {meta}")
    for principle in constitution.get("principles", [])[:3]:
        lines.append(f"  Principle: {_word_trim(principle, 120)}")
    for gate in constitution.get("quality_gates", [])[:2]:
        lines.append(f"  Gate: {_word_trim(gate, 120)}")
    governance = constitution.get("governance", [])
    if governance:
        lines.append(f"  Governance: {_word_trim(governance[0], 120)}")
    lines.append("")
    return lines


def _format_clarification_markdown_block(entry: dict | None) -> list[str]:
    """Render a Markdown clarification block for full briefing output."""
    clarify = _serialize_clarification(entry)
    if not clarify:
        return []
    lines = ["## ❓ Clarify Gate", ""]
    task = clarify.get("clarified_task", "")
    if task:
        lines.append(f"**Ready brief:** {task}")
        lines.append("")
    for index, question in enumerate(clarify.get("questions", [])[:5], 1):
        category = question.get("category", "?")
        lines.append(f"{index}. **{category}** — {question.get('question', '')}")
        options = question.get("options", [])
        if options:
            lines.append(f"   Options: {' | '.join(options[:4])}")
        lines.append("")
    return lines


def _format_constitution_markdown_block(entry: dict | None) -> list[str]:
    constitution = _serialize_constitution(entry)
    if not constitution:
        return []
    lines = ["## 🏛️ Constitution", ""]
    version = constitution.get("version", "")
    amended = constitution.get("last_amended", "")
    if version:
        lines.append(f"**Version:** {version}")
    if amended:
        lines.append(f"**Last Amended:** {amended}")
    if version or amended:
        lines.append("")
    for principle in constitution.get("principles", [])[:3]:
        lines.append(f"- **Principle:** {principle}")
    for gate in constitution.get("quality_gates", [])[:2]:
        lines.append(f"- **Quality Gate:** {gate}")
    for item in constitution.get("governance", [])[:1]:
        lines.append(f"- **Governance:** {item}")
    lines.append("")
    return lines


def _format_clarification_compact_block(entry: dict | None) -> list[str]:
    """Render an XML-like compact clarification block."""
    clarify = _serialize_clarification(entry)
    if not clarify:
        return []
    lines = ["<clarify>"]
    task = _word_trim(clarify.get("clarified_task", ""), 180)
    if task:
        lines.append(f"  <task>{_xml_escape(task)}</task>")
    for question in clarify.get("questions", [])[:5]:
        category = _xml_escape(question.get("category", "?"))
        prompt = _xml_escape(_word_trim(question.get("question", ""), 120))
        lines.append(f'  <question category="{category}">{prompt}</question>')
    lines.append("</clarify>")
    return lines


def _format_constitution_compact_block(entry: dict | None) -> list[str]:
    constitution = _serialize_constitution(entry)
    if not constitution:
        return []
    version = _xml_escape(constitution.get("version", ""))
    amended = _xml_escape(constitution.get("last_amended", ""))
    lines = [f'<constitution version="{version}" amended="{amended}">']
    for principle in constitution.get("principles", [])[:3]:
        lines.append(f"  <principle>{_xml_escape(_word_trim(principle, 120))}</principle>")
    for gate in constitution.get("quality_gates", [])[:2]:
        lines.append(f"  <gate>{_xml_escape(_word_trim(gate, 120))}</gate>")
    for item in constitution.get("governance", [])[:1]:
        lines.append(f"  <governance>{_xml_escape(_word_trim(item, 120))}</governance>")
    lines.append("</constitution>")
    return lines


def _format_compact(
    query: str,
    data: dict,
    past_work: list,
    categories: dict,
    blast: list = None,
    file_annotations: list | None = None,
    constitution_entry: dict | None = None,
    clarify_entry: dict | None = None,
) -> str:
    """Compact format optimized for AI agent context injection.

    Minimal-first ordering: mistakes → blast_radius → patterns/decisions/tools → past_work → file_index.
    Mistakes and blast radius appear first so the most actionable risk context
    is visible in the smallest token budget.
    """
    lines = []
    safe_query = query[:100].replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
    lines.append(f'<briefing task="{safe_query}">\n')
    lines.extend(_format_constitution_compact_block(constitution_entry))
    lines.extend(_format_clarification_compact_block(clarify_entry))

    def _cat_block(cat: str) -> None:
        """Append one XML-style category block to *lines*.

        Entries matching _STATUS_NOTE_RE (Wave-style progress/verification
        status notes stored as knowledge) are suppressed at read time without
        touching the DB.
        """
        entries = data.get(cat, [])
        if not entries:
            return
        rendered = []
        for entry in entries:
            # Cap at _COMPACT_MAX_PER_CAT real entries per category (issue #163).
            # Status-note entries suppressed below do NOT consume cap slots —
            # we check cap first so we stop iterating once we have enough.
            if len(rendered) >= _COMPACT_MAX_PER_CAT:
                break
            raw_title = entry.get("title", "")
            if _STATUS_NOTE_RE.search(raw_title):
                continue  # suppress persisted status-note rows (issue #163)
            title = _word_trim(raw_title, 80)
            content = entry.get("content", "")
            first_line = ""
            for ln in content.split("\n"):
                ln = ln.strip().lstrip("-").lstrip("*").lstrip("0123456789.").strip()
                if (
                    ln
                    and len(ln) > 15
                    and not ln.startswith("#")
                    and not ln.startswith("|")
                    and not ln.startswith(">")
                    and not ln.startswith("```")
                    and "phỏng vấn" not in ln.lower()
                    and "điểm" not in ln.lower()[:20]
                ):
                    first_line = ln[:200]
                    break
            if not first_line:
                first_line = content[:150].replace("\n", " ")
            # Suppress repeated-prefix: when first_line begins with the same text as
            # title (common when title is the truncated start of a long sentence),
            # showing both creates noise like "Wave19 … tou: Wave19 … touched …".
            title_prefix = title.rstrip(".… ").lower()
            recurrence = int(entry.get("recurrence_after_briefing") or 0)
            recurring_badge = f"[RECURRING×{recurrence}] " if recurrence > 0 else ""
            pinned_badge = "📌 " if (entry.get("priority") or "") == "P0" else ""
            if first_line.lower().startswith(title_prefix[:60]):
                rendered.append(f"- {pinned_badge}{recurring_badge}{title}")
            else:
                rendered.append(f"- {pinned_badge}{recurring_badge}{title}: {first_line}")
        if not rendered:
            return
        lines.append(f"<{cat}s>")
        lines.extend(rendered)
        lines.append(f"</{cat}s>\n")

    # 1. Mistakes first — highest-priority risk-avoidance signal
    _cat_block("mistake")

    # 2. Blast radius immediately after mistakes — grounded current-change risk
    if blast:
        lines.append("<blast_radius>")
        for b in blast:
            stale_tag = " (stale)" if b.get("stale") else ""
            lines.append(
                f"- {b['risk_emoji']} {b['risk_level']}: {b['file']}{stale_tag} "
                f"({b['mistakes']}m/{b['patterns']}p/{b['decisions']}d)"
            )
        lines.append("</blast_radius>\n")

    # 3. Patterns, decisions, tools — what to follow
    for cat in ("pattern", "decision", "tool"):
        _cat_block(cat)

    # 4. Past work last — least critical for minimal-first context injection
    if past_work:
        lines.append("<past_work>")
        for w in past_work:
            sid = w.get("session_id", "?")[:8]
            lines.append(f"- [{w.get('doc_type', '?')}] {w.get('title', '?')} (session {sid})")
        lines.append("</past_work>\n")

    # 5. File index — supplemental, minimal token cost
    if file_annotations:
        lines.append("<file_index>")
        for ann in file_annotations[:6]:
            fp = ann.get("file_path", "")
            desc = ann.get("description", "")
            tok = ann.get("est_tokens", 0)
            tok_str = f" ~{tok}tok" if tok else ""
            lines.append(f"- {fp}{tok_str}: {desc}")
        lines.append("</file_index>\n")

    lines.append("</briefing>")
    return "\n".join(lines)


def generate_titles_only(query: str = "", limit: int = 20, min_confidence: float = 0.3) -> str:
    """Progressive disclosure layer 1: titles + type + token cost only.

    Ultra-compact index (~10 tokens/entry) for scanning before drill-down.
    Use query-session.py --detail <id> for full content.
    """
    db = get_db()
    lines = []

    if query:
        # Search mode
        safe_query = _sanitize_fts_query(query)
        if safe_query:
            rows = db.execute(
                """
                SELECT ke.id, ke.category, ke.title, ke.est_tokens, ke.wing, ke.room
                FROM ke_fts fts
                JOIN knowledge_entries ke ON fts.rowid = ke.id
                WHERE ke_fts MATCH ?
                  AND ke.confidence >= ?
                ORDER BY rank
                LIMIT ?
            """,
                (safe_query, min_confidence, limit),
            ).fetchall()
        else:
            rows = []
    else:
        # Recent mode (no query)
        rows = db.execute(
            """
            SELECT id, category, title, est_tokens, wing, room
            FROM knowledge_entries
            WHERE confidence >= ?
            ORDER BY last_seen DESC
            LIMIT ?
        """,
            (min_confidence, limit),
        ).fetchall()

    if not rows:
        db.close()
        return "No entries found." + (" Try a different query." if query else "")

    header = f"📋 {len(rows)} entries"
    if query:
        header += f" matching '{query}'"
    lines.append(header)
    lines.append("")

    for r in rows:
        tok = f"~{r['est_tokens']}tok" if r["est_tokens"] else ""
        loc = ""
        if r["wing"] or r["room"]:
            parts = [r["wing"], r["room"]]
            loc = f" [{'/'.join(p for p in parts if p)}]"
        lines.append(f"  #{r['id']:4d} [{r['category']:9s}] {r['title'][:60]} {tok}{loc}")

    lines.append("")
    lines.append("→ query-session.py --detail <ID> for full content")

    db.close()
    return "\n".join(lines)


def generate_wakeup() -> str:
    """Ultra-compact wake-up summary (~170 tokens) for session start.

    Outputs key project context, current branch, top mistakes/patterns,
    and recent decisions in a terse format designed for AI consumption.
    """
    db = get_db()
    lines = []

    # Team & project info (from wakeup_config if available)
    try:
        row = db.execute("SELECT value FROM wakeup_config WHERE key='team'").fetchone()
        if row:
            lines.append(f"TEAM: {row[0]}")
        row = db.execute("SELECT value FROM wakeup_config WHERE key='project'").fetchone()
        if row:
            lines.append(f"PROJECT: {row[0]}")
    except Exception:
        pass  # wakeup_config may not exist yet

    # Current branch
    try:
        import subprocess

        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], stderr=subprocess.DEVNULL, text=True, timeout=5
        ).strip()
        lines.append(f"BRANCH: {branch}")
    except (subprocess.TimeoutExpired, Exception):
        lines.append("BRANCH: (unknown)")

    # Wakeup config overrides
    try:
        rows = db.execute("SELECT key, value FROM wakeup_config ORDER BY key").fetchall()
        for r in rows:
            lines.append(f"{r['key'].upper()}: {r['value']}")
    except sqlite3.OperationalError:
        pass

    # Top mistakes (3)
    try:
        rows = db.execute("""
            SELECT title FROM knowledge_entries
            WHERE category = 'mistake' AND confidence >= 0.5
            ORDER BY COALESCE(intensity, 0.5) DESC, confidence DESC, occurrence_count DESC
            LIMIT 3
        """).fetchall()
        if rows:
            items = " | ".join(f"({i + 1}) {r['title'][:50]}" for i, r in enumerate(rows))
            lines.append(f"TOP-MISTAKES: {items}")
    except sqlite3.OperationalError:
        try:
            rows = db.execute("""
                SELECT title FROM knowledge_entries
                WHERE category = 'mistake' AND confidence >= 0.5
                ORDER BY occurrence_count DESC, confidence DESC
                LIMIT 3
            """).fetchall()
            if rows:
                items = " | ".join(f"({i + 1}) {r['title'][:50]}" for i, r in enumerate(rows))
                lines.append(f"TOP-MISTAKES: {items}")
        except sqlite3.OperationalError:
            pass

    # Top patterns (3)
    try:
        rows = db.execute("""
            SELECT title FROM knowledge_entries
            WHERE category = 'pattern' AND confidence >= 0.5
            ORDER BY occurrence_count DESC, confidence DESC
            LIMIT 3
        """).fetchall()
        if rows:
            items = " | ".join(f"({i + 1}) {r['title'][:50]}" for i, r in enumerate(rows))
            lines.append(f"TOP-PATTERNS: {items}")
    except sqlite3.OperationalError:
        pass

    # Recent decisions (3)
    try:
        rows = db.execute("""
            SELECT title FROM knowledge_entries
            WHERE category = 'decision'
            ORDER BY last_seen DESC
            LIMIT 3
        """).fetchall()
        if rows:
            items = " | ".join(f"({i + 1}) {r['title'][:50]}" for i, r in enumerate(rows))
            lines.append(f"RECENT-DECISIONS: {items}")
    except sqlite3.OperationalError:
        pass

    # Last session summary (from most recent plan.md)
    try:
        _add_session_summary(lines)
    except Exception:
        pass

    # Staleness banner: shown only when >= 40% of knowledge entries are stale (>90 days)
    try:
        stale_count = db.execute(
            "SELECT COUNT(*) FROM knowledge_entries WHERE datetime(last_seen) < datetime('now', '-90 days')"
        ).fetchone()[0]
        total_count = db.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
        if total_count > 0 and stale_count / total_count >= 0.40:
            lines.append(f"⚠  {stale_count} stale entries (>90d) — run: sk knowledge evict --dry-run")
    except Exception:
        pass

    db.close()
    return "\n".join(lines)


def _add_session_summary(lines: list) -> None:
    """Extract summary from the most recent session's plan.md."""
    import subprocess as _sp

    session_state = Path.home() / ".copilot" / "session-state"
    if not session_state.exists():
        return

    # Find most recently modified session directory
    sessions = []
    for d in session_state.iterdir():
        if d.is_dir() and len(d.name) > 8 and "-" in d.name:
            plan = d / "plan.md"
            if plan.exists():
                try:
                    sessions.append((plan.stat().st_mtime, plan))
                except OSError:
                    pass

    if not sessions:
        return

    sessions.sort(reverse=True)
    plan_path = sessions[0][1]

    try:
        content = plan_path.read_text(encoding="utf-8", errors="replace")[:3000]
    except Exception:
        return

    summary_parts = []

    # Extract problem/task statement (first ## heading or "## Problem" section)
    for marker in ["## Problem", "## Task", "# Plan:"]:
        if marker in content:
            start = content.index(marker) + len(marker)
            # Get the first paragraph after the heading
            rest = content[start : start + 500].strip()
            first_para = rest.split("\n\n")[0].replace("\n", " ").strip()
            if first_para and len(first_para) > 10:
                summary_parts.append(f"LAST-TASK: {first_para[:120]}")
                break

    # Extract completed items from SQL todos or plan checkboxes
    # Try to find [x] items
    done_items = []
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("- [x]") or line.startswith("* [x]"):
            item = line[5:].strip()[:60]
            if item:
                done_items.append(item)

    if done_items:
        summary_parts.append(f"DONE: {' | '.join(done_items[:3])}")

    # Extract next steps / remaining items
    pending_items = []
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("- [ ]") or line.startswith("* [ ]"):
            item = line[5:].strip()[:60]
            if item:
                pending_items.append(item)

    if pending_items:
        summary_parts.append(f"NEXT: {' | '.join(pending_items[:3])}")

    lines.extend(summary_parts)


def search_by_wing_room(wing: str = "", room: str = "", limit: int = 10) -> str:
    """Search knowledge entries filtered by wing and/or room."""
    db = get_db()
    conditions = []
    params = []

    if wing:
        conditions.append("wing = ?")
        params.append(wing)
    if room:
        conditions.append("room = ?")
        params.append(room)

    if not conditions:
        db.close()
        return "Error: specify --wing and/or --room"

    where = " AND ".join(conditions)
    params.append(limit)

    rows = db.execute(
        f"""
        SELECT id, category, title, content, tags, wing, room, confidence
        FROM knowledge_entries
        WHERE {where}
        ORDER BY confidence DESC, occurrence_count DESC
        LIMIT ?
    """,
        params,
    ).fetchall()

    if not rows:
        db.close()
        return f"No entries found for wing={wing!r} room={room!r}"

    lines = [f"Found {len(rows)} entries (wing={wing!r} room={room!r}):\n"]
    for r in rows:
        first_line = r["content"].split("\n")[0][:120] if r["content"] else ""
        lines.append(f"  [{r['category']}] #{r['id']} {r['title']}")
        lines.append(f"    {first_line}")
    db.close()
    return "\n".join(lines)


def generate_task_briefing(task_id: str, limit: int = 30, fmt: str = "text", with_meta: bool = False):
    """Generate a focused briefing for a specific task ID.

    Pulls all knowledge entries tagged with this task_id and formats them
    as a compact recall surface, grouped by category.
    Also includes FTS-based related entries using the task_id as a query.
    """
    db = get_db()
    safe_task = task_id.strip()[:200]

    # Primary: entries explicitly tagged with this task_id
    try:
        tagged_rows = db.execute(
            """
            SELECT id, category, title, content, confidence,
                   affected_files, tags, occurrence_count,
                   document_id, source_section,
                   source_file, start_line, end_line, code_language, code_snippet,
                   source_doc_type, source_doc_title, source_doc_file_path, source_doc_seq
            FROM (
                SELECT ke.id, ke.category, ke.title, ke.content, ke.confidence,
                       ke.affected_files, ke.tags, ke.occurrence_count,
                       ke.document_id, ke.source_section,
                       ke.source_file, ke.start_line, ke.end_line, ke.code_language, ke.code_snippet,
                       d.doc_type as source_doc_type, d.title as source_doc_title,
                       d.file_path as source_doc_file_path, d.seq as source_doc_seq
                FROM knowledge_entries ke
                LEFT JOIN documents d ON ke.document_id = d.id
                WHERE ke.task_id = ?
                ORDER BY ke.confidence DESC, ke.occurrence_count DESC
                LIMIT ?
            )
        """,
            (safe_task, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        try:
            tagged_rows = db.execute(
                """
                SELECT id, category, title, content, confidence,
                       affected_files, tags, occurrence_count
                FROM knowledge_entries
                WHERE task_id = ?
                ORDER BY confidence DESC, occurrence_count DESC
                LIMIT ?
            """,
                (safe_task, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            tagged_rows = []

    # Secondary: FTS search using task_id as query terms (catches related entries)
    fts_query = _sanitize_fts_query(task_id)
    fts_rows = []
    tagged_ids = {r["id"] for r in tagged_rows}
    if fts_query and fts_query != '""':
        try:
            rows = db.execute(
                """
                SELECT ke.id, ke.category, ke.title, ke.content, ke.confidence,
                       ke.affected_files, ke.tags, ke.occurrence_count,
                       ke.document_id, ke.source_section,
                       ke.source_file, ke.start_line, ke.end_line, ke.code_language, ke.code_snippet,
                       d.doc_type as source_doc_type, d.title as source_doc_title,
                       d.file_path as source_doc_file_path, d.seq as source_doc_seq
                FROM ke_fts fts
                JOIN knowledge_entries ke ON fts.rowid = ke.id
                LEFT JOIN documents d ON ke.document_id = d.id
                WHERE ke_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """,
                (fts_query, min(limit, 10)),
            ).fetchall()
            for r in rows:
                if r["id"] not in tagged_ids:
                    fts_rows.append(r)
        except sqlite3.OperationalError:
            try:
                rows = db.execute(
                    """
                    SELECT ke.id, ke.category, ke.title, ke.content, ke.confidence,
                           ke.affected_files, ke.tags, ke.occurrence_count
                    FROM ke_fts fts
                    JOIN knowledge_entries ke ON fts.rowid = ke.id
                    WHERE ke_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                """,
                    (fts_query, min(limit, 10)),
                ).fetchall()
                for r in rows:
                    if r["id"] not in tagged_ids:
                        fts_rows.append(r)
            except sqlite3.OperationalError:
                pass

    selected_entry_ids = _safe_int_list([r["id"] for r in tagged_rows] + [r["id"] for r in fts_rows[:5]])
    hit_count = len(selected_entry_ids)

    # Per-entry recall stats — record before any db.close() path.
    _upsert_entry_recall_stats(db, selected_entry_ids, safe_task)

    if not tagged_rows and not fts_rows:
        if fmt == "json":
            output = json.dumps(
                {
                    "task_id": task_id,
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "total_entries": 0,
                    "tagged_entries": [],
                    "related_entries": [],
                },
                indent=2,
                ensure_ascii=False,
            )
            db.close()
            if with_meta:
                return output, {
                    "surface": "task_json",
                    "mode": "auto",
                    "raw_query": task_id,
                    "rewritten_query": safe_task,
                    "task_id": safe_task,
                    "selected_entry_ids": [],
                    "hit_count": 0,
                    "output_chars": len(output),
                }
            return output
        db.close()
        return (
            f"No knowledge entries found for task: '{task_id}'\n"
            f"Tip: Use 'learn.py --task {task_id!r} ...' to tag entries.\n"
            f"Or try: briefing.py '{task_id}' for FTS-based briefing."
        )

    lines = [f"📋 Task recall: {task_id}\n"]

    if tagged_rows:
        # Group tagged entries by category
        by_cat: dict = {}
        for r in tagged_rows:
            by_cat.setdefault(r["category"], []).append(r)

        cat_meta = {
            "mistake": "⚠️  Past Mistakes",
            "pattern": "✅ Proven Patterns",
            "decision": "🏗️  Architecture Decisions",
            "tool": "🔧 Tools & Configs",
            "feature": "✨ Features",
            "refactor": "♻️  Refactors",
            "discovery": "🔍 Discoveries",
        }
        for cat, label in cat_meta.items():
            entries = by_cat.get(cat, [])
            if not entries:
                continue
            lines.append(f"{label}")
            for e in entries:
                eid = e["id"]
                title = e["title"][:80]
                files = ""
                try:
                    fl = json.loads(e["affected_files"] or "[]")
                    if fl:
                        files = f"  → {', '.join(fl[:2])}"
                except Exception:
                    pass
                lines.append(f"  #{eid} {title}{files}")
                prov = _source_label_from_row(e)
                if prov:
                    lines.append(f"    {prov}")
                loc = _code_location_label_from_row(e)
                if loc:
                    lines.append(f"    {loc}")
            lines.append("")

        # Render any categories not in the hardcoded map (e.g. custom categories)
        unknown_cats = [c for c in by_cat if c not in cat_meta]
        if unknown_cats:
            lines.append("📌 Other")
            for cat in unknown_cats:
                for e in by_cat[cat]:
                    eid = e["id"]
                    title = e["title"][:80]
                    lines.append(f"  #{eid} [{cat}] {title}")
                    prov = _source_label_from_row(e)
                    if prov:
                        lines.append(f"    {prov}")
                    loc = _code_location_label_from_row(e)
                    if loc:
                        lines.append(f"    {loc}")
            lines.append("")

    if fts_rows:
        lines.append("🔗 Related entries (FTS match on task name)")
        for r in fts_rows[:5]:
            lines.append(f"  #{r['id']} [{r['category']}] {r['title'][:75]}")
            prov = _source_label_from_row(r)
            if prov:
                lines.append(f"    {prov}")
            loc = _code_location_label_from_row(r)
            if loc:
                lines.append(f"    {loc}")
        lines.append("")

    total = len(tagged_rows) + len(fts_rows)
    lines.append(f"({total} entries) Use query-session.py --task {task_id!r} for full detail")

    if fmt == "json":
        # Machine-readable: return structured JSON instead of text
        def _parse_files(raw):
            try:
                return json.loads(raw or "[]")
            except Exception:
                return []

        json_tagged = [
            {
                "id": r["id"],
                "category": r["category"],
                "title": r["title"],
                "content": r["content"][:500] if r["content"] else "",
                "confidence": r["confidence"],
                "tags": r["tags"] or "",
                "affected_files": _parse_files(r["affected_files"]),
                "occurrence_count": r["occurrence_count"],
                "source_document": _source_document_from_row(r),
                "source_file": r["source_file"] if "source_file" in r.keys() else None,
                "start_line": r["start_line"] if "start_line" in r.keys() else None,
                "end_line": r["end_line"] if "end_line" in r.keys() else None,
                "code_language": r["code_language"] if "code_language" in r.keys() else "",
                "code_snippet": (r["code_snippet"] or "")[:2000] if "code_snippet" in r.keys() else "",
                "snippet_freshness": _compute_snippet_freshness(r),
                "related_entry_ids": _related_entry_ids_for_entry(int(r["id"]), db=db),
            }
            for r in tagged_rows
        ]
        json_related = [
            {
                "id": r["id"],
                "category": r["category"],
                "title": r["title"],
                "confidence": r["confidence"],
                "affected_files": _parse_files(r["affected_files"]),
                "source_document": _source_document_from_row(r),
                "source_file": r["source_file"] if "source_file" in r.keys() else None,
                "start_line": r["start_line"] if "start_line" in r.keys() else None,
                "end_line": r["end_line"] if "end_line" in r.keys() else None,
                "code_language": r["code_language"] if "code_language" in r.keys() else "",
                "code_snippet": (r["code_snippet"] or "")[:2000] if "code_snippet" in r.keys() else "",
                "snippet_freshness": _compute_snippet_freshness(r),
                "related_entry_ids": _related_entry_ids_for_entry(int(r["id"]), db=db),
            }
            for r in fts_rows[:5]
        ]
        output = json.dumps(
            {
                "task_id": task_id,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "total_entries": total,
                "tagged_entries": json_tagged,
                "related_entries": json_related,
            },
            indent=2,
            ensure_ascii=False,
        )
        db.close()
        if with_meta:
            return output, {
                "surface": "task_json",
                "mode": "auto",
                "raw_query": task_id,
                "rewritten_query": safe_task,
                "task_id": safe_task,
                "selected_entry_ids": selected_entry_ids,
                "hit_count": hit_count,
                "output_chars": len(output),
            }
        return output

    output = "\n".join(lines)
    db.close()
    if with_meta:
        return output, {
            "surface": "standard",
            "mode": "auto",
            "raw_query": task_id,
            "rewritten_query": safe_task,
            "task_id": safe_task,
            "selected_entry_ids": selected_entry_ids,
            "hit_count": hit_count,
            "output_chars": len(output),
        }
    return output


# Built-in briefing presets (issue #714).
# Each preset maps a name to a list of CLI flags that are injected into args
# before the rest of main() parses them.  Flags already supplied by the caller
# take precedence — preset values are only injected when the flag is absent.
BRIEFING_PRESETS: dict[str, list[str]] = {
    "daily": ["--wakeup", "--pinned", "--days", "1"],
    "sprint": ["--days", "7"],
    "debug": ["--days", "30"],
}


def generate_briefing_history(days: int = 7, fmt: str = "text") -> str:
    """Return a recall history report grouped by day (issue #720).

    Shows: date | entries recalled that day | top-3 entry titles.
    Uses entry_recall_day_log (one row per entry_id + calendar day) joined to
    knowledge_entries for titles and categories.  Falls back gracefully when the
    recall tables do not exist yet.
    """
    if not DB_PATH.exists():
        msg = "No knowledge database found — recall history unavailable."
        if fmt == "json":
            return json.dumps({"error": msg, "days": []})
        return msg

    try:
        db = sqlite3.connect(str(DB_PATH))
        db.row_factory = sqlite3.Row
    except Exception as exc:
        msg = f"Cannot open database: {exc}"
        if fmt == "json":
            return json.dumps({"error": msg, "days": []})
        return msg

    try:
        # Check table exists
        row = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='entry_recall_day_log'").fetchone()
        if row is None:
            msg = "Recall history is not available yet — no recall events have been recorded."
            if fmt == "json":
                return json.dumps({"error": msg, "days": []})
            return msg

        cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).strftime("%Y-%m-%d")

        # Fetch all (day, entry_id, title, category) rows within the window
        rows = db.execute(
            """
            SELECT d.day, ke.id AS entry_id, ke.title, ke.category
            FROM entry_recall_day_log d
            JOIN knowledge_entries ke ON d.entry_id = ke.id
            WHERE d.day >= ?
            ORDER BY d.day DESC, ke.title
            """,
            (cutoff,),
        ).fetchall()

        # Group by day
        from collections import defaultdict

        day_map: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            day_map[r["day"]].append({"entry_id": r["entry_id"], "title": r["title"], "category": r["category"]})

        # Sort days descending
        sorted_days = sorted(day_map.keys(), reverse=True)

        if fmt == "json":
            result = []
            for day in sorted_days:
                entries = day_map[day]
                result.append(
                    {
                        "date": day,
                        "entries_recalled": len(entries),
                        "top_titles": [e["title"] for e in entries[:3]],
                        "entries": entries,
                    }
                )
            return json.dumps({"days": result, "window_days": days}, indent=2)

        # Text / table output
        if not sorted_days:
            return f"No recall events in the last {days} day(s)."

        lines = [f"## Recall History — last {days} day(s)\n"]
        for day in sorted_days:
            entries = day_map[day]
            top3 = [e["title"] for e in entries[:3]]
            lines.append(f"### {day}  ({len(entries)} entries recalled)")
            for t in top3:
                lines.append(f"  - {t}")
            if len(entries) > 3:
                lines.append(f"  … and {len(entries) - 3} more")
            lines.append("")
        return "\n".join(lines)

    except Exception as exc:
        msg = f"Error reading recall history: {exc}"
        if fmt == "json":
            return json.dumps({"error": msg, "days": []})
        return msg
    finally:
        try:
            db.close()
        except Exception:
            pass


def generate_never_recalled(fmt: str = "text") -> str:
    """Return knowledge entries that have never been recalled (issue #720).

    Uses a LEFT JOIN between knowledge_entries and entry_recall_day_log so that
    entries with no recall events (NULL join side) are surfaced.  Falls back
    gracefully when the recall tables do not exist yet.
    """
    if not DB_PATH.exists():
        msg = "No knowledge database found."
        if fmt == "json":
            return json.dumps({"error": msg, "entries": []})
        return msg

    try:
        db = sqlite3.connect(str(DB_PATH))
        db.row_factory = sqlite3.Row
    except Exception as exc:
        msg = f"Cannot open database: {exc}"
        if fmt == "json":
            return json.dumps({"error": msg, "entries": []})
        return msg

    try:
        # Check table exists
        row = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='entry_recall_day_log'").fetchone()
        if row is None:
            msg = "Recall tracking is not available yet — no recall events have been recorded."
            if fmt == "json":
                return json.dumps({"error": msg, "entries": []})
            return msg

        rows = db.execute(
            """
            SELECT ke.id, ke.title, ke.category, ke.priority
            FROM knowledge_entries ke
            LEFT JOIN entry_recall_day_log r ON ke.id = r.entry_id
            WHERE r.entry_id IS NULL
            ORDER BY ke.created_at DESC
            LIMIT 50
            """,
        ).fetchall()

        if fmt == "json":
            entries = [
                {"id": r["id"], "title": r["title"], "category": r["category"], "priority": r["priority"]} for r in rows
            ]
            return json.dumps({"entries": entries, "count": len(entries)}, indent=2)

        if not rows:
            return "All knowledge entries have been recalled at least once. 🎉"

        lines = [f"## Never-Recalled Entries  ({len(rows)} total)\n"]
        lines.append(f"{'ID':<6}  {'Category':<12}  Title")
        lines.append("-" * 60)
        for r in rows:
            cat = (r["category"] or "")[:12]
            title = (r["title"] or "")[:60]
            lines.append(f"{r['id']:<6}  {cat:<12}  {title}")
        return "\n".join(lines)

    except Exception as exc:
        msg = f"Error reading never-recalled entries: {exc}"
        if fmt == "json":
            return json.dumps({"error": msg, "entries": []})
        return msg
    finally:
        try:
            db.close()
        except Exception:
            pass


def _query_code_context(db_path: Path, query: str, token_budget: int = 1000) -> list[dict]:
    """Query code_fts for relevant snippets. Returns [] if table missing or error."""
    try:
        db = sqlite3.connect(str(db_path))
        has = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='code_index'").fetchone()
        if not has:
            db.close()
            return []
        char_budget = token_budget * 4
        results = []
        safe_q = re.sub(r'["*]|\b(?:OR|AND|NOT|NEAR)\b', " ", query, flags=re.IGNORECASE).strip()
        if safe_q:
            try:
                rows = db.execute(
                    """SELECT ci.file_path, ci.language, ci.start_line, ci.symbol_name,
                              ci.content_snippet
                       FROM code_fts fts JOIN code_index ci ON fts.rowid = ci.id
                       WHERE code_fts MATCH ? ORDER BY rank LIMIT 10""",
                    [f'"{safe_q}"'],
                ).fetchall()
            except sqlite3.OperationalError:
                rows = db.execute(
                    """SELECT file_path, language, start_line, symbol_name, content_snippet
                       FROM code_index
                       WHERE LOWER(content_snippet) LIKE ? OR LOWER(symbol_name) LIKE ?
                       LIMIT 10""",
                    [f"%{query.lower()}%", f"%{query.lower()}%"],
                ).fetchall()
            used = 0
            for r in rows:
                snippet = r[4] if isinstance(r, tuple) else r["content_snippet"]
                if used + len(snippet) > char_budget:
                    break
                results.append(
                    {
                        "file_path": r[0],
                        "language": r[1],
                        "start_line": r[2],
                        "symbol_name": r[3],
                        "content": snippet,
                    }
                )
                used += len(snippet)
        db.close()
        return results
    except Exception:
        return []


def _format_code_context(snippets: list[dict]) -> str:
    """Format code snippets as fenced markdown blocks."""
    if not snippets:
        return ""
    lines = ["\n## Relevant Code Context"]
    for s in snippets:
        lang = s.get("language", "")
        fname = s.get("file_path", "")
        lineno = s.get("start_line", 0)
        sym = s.get("symbol_name", "")
        lines.append(f"\n### {fname}:{lineno} — {sym} ({lang})")
        lines.append(f"```{lang}")
        lines.append(s.get("content", "").rstrip())
        lines.append("```")
    return "\n".join(lines)


def _parse_window(window: str) -> int:
    """Parse duration string like 1d, 7d, 24h, 2w into seconds."""
    import re

    m = re.fullmatch(r"(\d+)([dhwm])", window.strip().lower())
    if not m:
        raise ValueError(f"Invalid window: {window!r}. Use format like 1d, 7d, 24h, 2w")
    n, unit = int(m.group(1)), m.group(2)
    multipliers = {"h": 3600, "d": 86400, "w": 604800, "m": 2592000}
    return n * multipliers[unit]


def _get_delta_entries(db: "sqlite3.Connection", window_secs: int) -> dict:
    """Get entries new/updated in the given time window."""
    import time

    cutoff = time.time() - window_secs

    new = db.execute(
        "SELECT id, category, title, content, tags, confidence, first_seen FROM knowledge_entries "
        "WHERE first_seen >= ? ORDER BY first_seen DESC LIMIT 20",
        (cutoff,),
    ).fetchall()

    updated = db.execute(
        "SELECT id, category, title, content, tags, confidence, first_seen, last_seen FROM knowledge_entries "
        "WHERE last_seen >= ? AND first_seen < ? ORDER BY last_seen DESC LIMIT 20",
        (cutoff, cutoff),
    ).fetchall()

    return {"new": new, "updated": updated, "window_secs": window_secs}


def _format_delta(delta_data: dict) -> str:
    """Format delta report showing new/updated entries in the time window."""
    window_secs = delta_data["window_secs"]
    new_entries = delta_data["new"]
    updated_entries = delta_data["updated"]

    # Human-readable window label
    if window_secs % 2592000 == 0:
        label = f"{window_secs // 2592000}m"
    elif window_secs % 604800 == 0:
        label = f"{window_secs // 604800}w"
    elif window_secs % 86400 == 0:
        label = f"{window_secs // 86400}d"
    else:
        label = f"{window_secs // 3600}h"

    lines = [f"## Delta Report (last {label})", ""]

    def _entry_line(row) -> str:
        row_dict = dict(row) if hasattr(row, "keys") else row
        if isinstance(row_dict, dict):
            eid = row_dict.get("id", "?")
            cat = row_dict.get("category", "entry")
            title = row_dict.get("title", "")
        else:
            # sqlite3.Row accessed by index: id=0, category=1, title=2
            eid, cat, title = row[0], row[1], row[2]
        title_truncated = (title[:77] + "...") if len(title) > 80 else title
        return f"[{cat}] #{eid} — {title_truncated}"

    lines.append(f"### New entries ({len(new_entries)})")
    if new_entries:
        for row in new_entries:
            lines.append(_entry_line(row))
    else:
        lines.append("(none)")
    lines.append("")

    lines.append(f"### Updated entries ({len(updated_entries)})")
    if updated_entries:
        for row in updated_entries:
            lines.append(_entry_line(row))
    else:
        lines.append("(none)")

    if not new_entries and not updated_entries:
        lines.append("")
        lines.append("(No entries found in window)")

    return "\n".join(lines)


def _delta_report(db_path: "Path", window: str) -> None:
    """Print a delta report of new/updated entries within the given time window."""
    try:
        window_secs = _parse_window(window)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if not db_path.exists():
        print("No knowledge database found.")
        return

    try:
        db = sqlite3.connect(str(db_path))
        db.row_factory = sqlite3.Row
    except Exception as exc:
        print(f"Cannot open database: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        delta_data = _get_delta_entries(db, window_secs)
    finally:
        db.close()

    print(_format_delta(delta_data))


def _recall_quality_report(db_path, days: int, as_json: bool) -> None:
    """Analyze recall quality from recall_events and search_feedback."""
    if not db_path.exists():
        if as_json:
            print(json.dumps({"error": "No knowledge database found."}))
        else:
            print("No knowledge database found.")
        return

    try:
        db = sqlite3.connect(str(db_path) + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
    except Exception as exc:
        if as_json:
            print(json.dumps({"error": f"Cannot open database: {exc}"}))
        else:
            print(f"Cannot open database: {exc}")
        return

    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "recall_events" not in tables:
        if as_json:
            print(json.dumps({"error": "No recall_events yet — run sk briefing first"}))
        else:
            print("No recall events yet — run 'sk briefing' first to populate recall data.")
        db.close()
        return

    recall_event_count = db.execute("SELECT COUNT(*) FROM recall_events").fetchone()[0]
    if recall_event_count == 0:
        message = "No recall events recorded yet. Use briefing to generate recall data first."
        if as_json:
            print(
                json.dumps(
                    {
                        "message": message,
                        "days": days,
                        "total_recall_events": 0,
                        "precision_by_query": [],
                        "dead_knowledge_count": 0,
                        "dead_knowledge": [],
                        "pin_candidates": [],
                        "category_breakdown": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(message)
        db.close()
        return

    cutoff = f"-{days} days"

    # 1. Precision by query (from search_feedback verdicts)
    precision_by_query = []
    if "search_feedback" in tables:
        rows = db.execute(
            """
            SELECT query,
                   COUNT(*) AS total,
                   SUM(CASE WHEN verdict = 1 THEN 1 ELSE 0 END) AS good,
                   SUM(CASE WHEN verdict = -1 THEN 1 ELSE 0 END) AS bad
            FROM search_feedback
            WHERE result_kind IN ('briefing', 'knowledge')
              AND date(created_at) >= date('now', ?)
              AND query IS NOT NULL AND query != ''
            GROUP BY query
            HAVING total >= 2
            ORDER BY CAST(good AS REAL)/total DESC
            """,
            (cutoff,),
        ).fetchall()
        for r in rows:
            pct = round(r["good"] / r["total"] * 100) if r["total"] else 0
            precision_by_query.append(
                {
                    "query": r["query"],
                    "total": r["total"],
                    "good": r["good"],
                    "bad": r["bad"],
                    "precision_pct": pct,
                }
            )

    # 2. Dead knowledge — entries never in any recall event's selected_entry_ids
    dead_entries = []
    if "knowledge_entries" in tables:
        selected_raw = db.execute(
            "SELECT selected_entry_ids FROM recall_events WHERE selected_entry_ids != '[]'"
        ).fetchall()
        ever_recalled: set[str] = set()
        for row in selected_raw:
            try:
                ids = json.loads(row[0])
                if isinstance(ids, list):
                    ever_recalled.update(str(i) for i in ids)
            except (json.JSONDecodeError, TypeError):
                pass

        dead_rows = db.execute(
            """SELECT id, title, category, priority, COALESCE(last_seen, first_seen) AS seen_at
               FROM knowledge_entries
               WHERE date(COALESCE(last_seen, first_seen)) <= date('now', ?)
               ORDER BY COALESCE(last_seen, first_seen) ASC""",
            (cutoff,),
        ).fetchall()
        dead_entries = [
            {"id": r["id"], "title": r["title"], "type": r["category"], "priority": r["priority"]}
            for r in dead_rows
            if str(r["id"]) not in ever_recalled
        ]

    # 3. Always-hit entries — in selected_entry_ids in >80% of events in window
    pin_candidates = []
    window_events = db.execute(
        "SELECT selected_entry_ids FROM recall_events WHERE date(created_at) >= date('now', ?)",
        (cutoff,),
    ).fetchall()
    total_window = len(window_events)
    if total_window >= 5:
        freq: dict[str, int] = {}
        for row in window_events:
            try:
                ids = json.loads(row[0])
                if isinstance(ids, list):
                    for eid in ids:
                        key = str(eid)
                        freq[key] = freq.get(key, 0) + 1
            except (json.JSONDecodeError, TypeError):
                pass
        threshold = 0.8
        for eid, cnt in sorted(freq.items(), key=lambda x: -x[1]):
            if cnt / total_window >= threshold:
                title = "(unknown)"
                if "knowledge_entries" in tables:
                    row = db.execute("SELECT title FROM knowledge_entries WHERE id=?", (eid,)).fetchone()
                    if row:
                        title = row["title"]
                pin_candidates.append(
                    {
                        "id": eid,
                        "title": title,
                        "hit_rate_pct": round(cnt / total_window * 100),
                        "hits": cnt,
                        "total_events": total_window,
                    }
                )

    # 4. Category breakdown
    category_breakdown = []
    if "knowledge_entries" in tables and total_window > 0:
        cat_rows = db.execute(
            """SELECT ke.category AS category, COUNT(DISTINCT re.id) AS events
               FROM recall_events re
               JOIN knowledge_entries ke ON ke.id = re.opened_entry_id
               WHERE date(re.created_at) >= date('now', ?)
               GROUP BY ke.category
               ORDER BY events DESC""",
            (cutoff,),
        ).fetchall()
        category_breakdown = [{"category": r["category"], "events": r["events"]} for r in cat_rows]

    db.close()

    if as_json:
        output = {
            "days": days,
            "total_recall_events": total_window,
            "precision_by_query": precision_by_query,
            "dead_knowledge_count": len(dead_entries),
            "dead_knowledge": dead_entries[:20],
            "pin_candidates": pin_candidates,
            "category_breakdown": category_breakdown,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    print(f"\nRecall Quality Report — last {days} days")
    print("━" * 44)
    print(f"  Total recall events : {total_window}")

    if precision_by_query:
        print("\nBy query precision (feedback-based):")
        high = [q for q in precision_by_query if q["precision_pct"] >= 70]
        low = [q for q in precision_by_query if q["precision_pct"] < 40]
        for q in high[:5]:
            bar = "█" * round(q["precision_pct"] / 10) + "░" * (10 - round(q["precision_pct"] / 10))
            print(f"  ✅ {q['query'][:40]:<40} {q['precision_pct']:3}%  {bar}  ({q['total']} hits)")
        if low:
            print("\nLow-precision queries (< 40%):")
            for q in low[:5]:
                print(f"  ⚠  {q['query'][:40]:<40} {q['precision_pct']:3}%  ({q['total']} hits, {q['bad']} bad)")
    else:
        print("\n  No feedback data yet — use 'python3 briefing.py --feedback \"<query>\" good|bad' to train.")

    print(f"\nDead knowledge (never recalled, older than {days}d): {len(dead_entries)} entries")
    if dead_entries:
        print("  → Consider running: sk knowledge evict --dry-run")
        for e in dead_entries[:3]:
            print(f"    #{e['id']} [{e['priority']}] {e['title'][:60]}")
        if len(dead_entries) > 3:
            print(f"    ... and {len(dead_entries) - 3} more")

    if pin_candidates:
        print("\nAuto-pin candidates (hit rate ≥ 80%):")
        for p in pin_candidates[:5]:
            print(f"  📌 #{p['id']} {p['title'][:50]} — {p['hit_rate_pct']}% ({p['hits']}/{p['total_events']} events)")
        print("  → Run: sk knowledge pin <id>")

    if category_breakdown:
        print("\nCategory recall breakdown:")
        for c in category_breakdown:
            print(f"  {c['category']:<15} {c['events']} events")

    print()


def _fetch_reflect_entries(db: sqlite3.Connection, question: str, limit: int = 15) -> list[dict]:
    """Fetch top entries relevant to the reflect question via FTS5."""
    safe_q = re.sub(r'["\*\(\)]', " ", question)[:100].strip()
    try:
        rows = db.execute(
            "SELECT ke.id, ke.category, ke.title, ke.content, ke.tags, ke.confidence "
            "FROM knowledge_entries ke "
            "JOIN knowledge_fts kf ON ke.id = kf.rowid "
            "WHERE knowledge_fts MATCH ? "
            "ORDER BY rank LIMIT ?",
            (safe_q, limit),
        ).fetchall()
    except Exception:
        rows = db.execute(
            "SELECT id, category, title, content, tags, confidence FROM knowledge_entries "
            "ORDER BY confidence DESC, last_seen DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {"id": r[0], "category": r[1], "title": r[2], "content": r[3], "tags": r[4], "confidence": r[5]} for r in rows
    ]


def _statistical_reflect(entries: list[dict], question: str) -> str:
    """Fallback: pattern-find via tag/title frequency when no LLM available."""
    import collections

    tag_counts: collections.Counter = collections.Counter()
    for e in entries:
        for tag in (e.get("tags") or "").split(","):
            t = tag.strip()
            if t:
                tag_counts[t] += 1

    mistake_count = sum(1 for e in entries if e.get("category") == "mistake")
    pattern_count = sum(1 for e in entries if e.get("category") == "pattern")
    top_tags = [f"{t}({c})" for t, c in tag_counts.most_common(5)]

    lines = [
        f"## Reflection: {question[:80]}",
        "",
        f"Based on {len(entries)} related entries:",
        f"  \u2022 {mistake_count} mistakes, {pattern_count} patterns",
        f"  \u2022 Top tags: {', '.join(top_tags) or 'none'}",
        "",
        "### Entry titles:",
    ]
    for e in entries[:8]:
        lines.append(f"  [{e['category']}] {e['title'][:70]}")
    return "\n".join(lines)


def _run_reflect(db_path: str, question: str, store: bool = True) -> None:
    """Run --reflect mode: fetch entries, synthesize insight, optionally store."""
    db = sqlite3.connect(db_path)
    entries = _fetch_reflect_entries(db, question)

    if not entries:
        print("No relevant entries found for reflection.")
        db.close()
        return

    # Statistical fallback (always works; LLM path is future extension)
    output = _statistical_reflect(entries, question)
    print(output)

    # Optionally store as discovery entry
    if store and entries:
        import time

        summary = f"Reflection on: {question[:60]}\n" + "\n".join(
            f"- [{e['category']}] {e['title']}" for e in entries[:5]
        )
        db.execute(
            "INSERT OR IGNORE INTO knowledge_entries "
            "(category, title, content, tags, confidence, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("discovery", f"Reflect: {question[:60]}", summary, "reflect,discovery", 0.6, time.time(), time.time()),
        )
        db.commit()
    db.close()


def main():
    args = sys.argv[1:]

    # ── --preset <name>: inject built-in preset flags (issue #714) ───────────
    # Processed before any other flag so preset defaults are established early.
    # Caller-supplied flags take precedence: a preset flag is only appended when
    # the same flag is not already present in args.
    if "--preset" in args:
        _p_idx = args.index("--preset")
        _p_name = args[_p_idx + 1] if _p_idx + 1 < len(args) else ""
        if not _p_name or _p_name not in BRIEFING_PRESETS:
            _known = ", ".join(sorted(BRIEFING_PRESETS))
            print(f"Error: unknown preset '{_p_name}'. Known presets: {_known}", file=sys.stderr)
            sys.exit(1)
        # Remove --preset <name> from args.
        args = args[:_p_idx] + args[_p_idx + 2 :]
        # Append preset flags not already supplied by the caller.
        _preset_flags = BRIEFING_PRESETS[_p_name]
        _i = 0
        while _i < len(_preset_flags):
            _flag = _preset_flags[_i]
            # Determine if the next token is a value (not a flag).
            _has_value = _i + 1 < len(_preset_flags) and not _preset_flags[_i + 1].startswith("--")
            if _flag not in args:
                if _has_value:
                    args = args + [_flag, _preset_flags[_i + 1]]
                else:
                    args = args + [_flag]
            _i += 2 if _has_value else 1
    # ── end --preset ─────────────────────────────────────────────────────────

    # ── Level 0 skill index (issue #118): detect --session-start early ──────
    # Strip the flag so it is never treated as a query term by later argument
    # parsing.  Print the skill index immediately — before any early-return paths
    # and before DB access that may sys.exit — so callers that capture stdout
    # still receive the index even when the knowledge DB is absent.
    session_start_mode = "--session-start" in args
    if session_start_mode:
        args = [a for a in args if a != "--session-start"]
        _skill_idx = _generate_skill_index()
        if _skill_idx:
            print(_skill_idx)
            sys.stdout.flush()  # ensure skill index is visible before any timeout
    # ── end Level 0 ──────────────────────────────────────────────────────────

    if not args or "--help" in args or "-h" in args:
        print(__doc__)
        return

    # Handle --wakeup mode (ultra-compact, no query needed)
    if "--wakeup" in args:
        no_danger = "--no-danger" in args
        if not no_danger and DB_PATH.exists():
            try:
                _db = sqlite3.connect(str(DB_PATH))
                _db.row_factory = sqlite3.Row
                danger = _fetch_danger_lane(_db)
                if danger:
                    print(danger)
                    print()
            except Exception:
                pass
        print(generate_wakeup())
        return

    # Handle --history [--days N] mode (issue #720)
    if "--history" in args:
        _hist_days = 7
        if "--days" in args:
            _hist_idx = args.index("--days")
            try:
                _hist_days = (
                    int(args[_hist_idx + 1])
                    if _hist_idx + 1 < len(args) and not args[_hist_idx + 1].startswith("--")
                    else 7
                )
            except (ValueError, IndexError):
                _hist_days = 7
        _hist_fmt = "json" if "--json" in args else "text"
        print(generate_briefing_history(days=_hist_days, fmt=_hist_fmt))
        return

    # Handle --never-recalled mode (issue #720)
    if "--never-recalled" in args:
        _nr_fmt = "json" if "--json" in args else "text"
        print(generate_never_recalled(fmt=_nr_fmt))
        return

    # Handle --recall-quality mode (issue #757)
    if "--recall-quality" in args:
        _rq_days = 30
        if "--days" in args:
            _rq_idx = args.index("--days")
            try:
                _rq_days = (
                    int(args[_rq_idx + 1]) if _rq_idx + 1 < len(args) and not args[_rq_idx + 1].startswith("--") else 30
                )
            except (ValueError, IndexError):
                _rq_days = 30
        _rq_days = max(1, _rq_days)
        _rq_json = "--json" in args
        _recall_quality_report(DB_PATH, _rq_days, _rq_json)
        return

    # Handle --delta <window> mode (issue #785)
    if "--delta" in args:
        _delta_idx = args.index("--delta")
        _delta_window = (
            args[_delta_idx + 1] if _delta_idx + 1 < len(args) and not args[_delta_idx + 1].startswith("--") else ""
        )
        if not _delta_window:
            print("Error: --delta requires a window argument like 1d, 7d, 24h, 2w", file=sys.stderr)
            sys.exit(1)
        _delta_report(DB_PATH, _delta_window)
        return

    # Handle --reflect <question> mode (issue #803)
    if "--reflect" in args:
        _rf_idx = args.index("--reflect")
        _rf_question = args[_rf_idx + 1] if _rf_idx + 1 < len(args) and not args[_rf_idx + 1].startswith("--") else ""
        if not _rf_question:
            print("Error: --reflect requires a question string", file=sys.stderr)
            return
        _rf_store = "--no-store" not in args
        _run_reflect(str(DB_PATH), _rf_question, store=_rf_store)
        return

    # Handle --titles-only mode (progressive disclosure layer 1)
    if "--titles-only" in args:
        limit = 20
        if "--limit" in args:
            idx = args.index("--limit")
            limit = int(args[idx + 1]) if idx + 1 < len(args) else 20
        # Consume values that follow known value-carrying flags so they don't
        # leak into the query string (e.g. --agent-tag <value>).
        _titles_consumed: set[int] = set()
        for _ti, _ta in enumerate(args):
            if _ta in ("--limit", "--agent-tag", "--msg-tag") and _ti + 1 < len(args):
                _titles_consumed.add(_ti + 1)
        query_parts = [
            a for i, a in enumerate(args) if i not in _titles_consumed and not a.startswith("--") and a != str(limit)
        ]
        query = " ".join(query_parts)
        print(generate_titles_only(query=query, limit=limit))
        return

    # Handle --task mode: task-scoped recall
    if "--task" in args:
        idx = args.index("--task")
        task_id = args[idx + 1] if idx + 1 < len(args) else ""
        if not task_id:
            print("Error: --task requires a task ID")
            return
        limit = 30
        if "--limit" in args:
            idx2 = args.index("--limit")
            limit = int(args[idx2 + 1]) if idx2 + 1 < len(args) else 30
        task_fmt = "json" if "--json" in args else "text"
        task_explicit_budget = 0
        if "--budget" in args:
            idx3 = args.index("--budget")
            try:
                task_explicit_budget = int(args[idx3 + 1]) if idx3 + 1 < len(args) else 3000
            except ValueError:
                task_explicit_budget = 3000
        task_avail_tokens = 0
        if "--available-tokens" in args:
            idx4 = args.index("--available-tokens")
            if idx4 + 1 < len(args) and not args[idx4 + 1].startswith("--"):
                try:
                    task_avail_tokens = int(args[idx4 + 1])
                except ValueError:
                    task_avail_tokens = 0
        budget = _compute_dynamic_budget(task_explicit_budget, task_avail_tokens)
        task_meta = None
        if task_fmt == "json":
            output, task_meta = generate_task_briefing(task_id, limit=limit, fmt=task_fmt, with_meta=True)
        else:
            output = generate_task_briefing(task_id, limit=limit, fmt=task_fmt)
        if budget > 0 and len(output) > budget:
            # Token tracking: measure initial injected size before reduction.
            task_injected_tokens = _estimate_tokens(len(output))
            task_budget_tokens = _estimate_tokens(budget)
            # Progressive limit reduction: keep complete entries, highest-confidence first.
            # Mirrors the main-path degradation strategy so --task is not a second-class path.
            for reduced_limit in range(max(1, limit - 1), 0, -1):
                if task_fmt == "json":
                    output, task_meta = generate_task_briefing(
                        task_id, limit=reduced_limit, fmt=task_fmt, with_meta=True
                    )
                else:
                    output = generate_task_briefing(task_id, limit=reduced_limit, fmt=task_fmt)
                if len(output) <= budget:
                    break
            # Final fallback: line-boundary truncation for text only (never JSON).
            if len(output) > budget and task_fmt != "json":
                footer = f"\n[BUDGET {budget} chars / ~{task_budget_tokens} tok — injected ~{task_injected_tokens} tok → hard-truncated to fit]"
                avail = budget - len(footer)
                body = output[: max(0, avail)].rsplit("\n", 1)[0] if avail > 0 else ""
                output = (body + footer)[:budget]
            elif task_fmt != "json" and task_injected_tokens > task_budget_tokens:
                footer = f"\n[BUDGET ~{task_budget_tokens} tok — reduced from ~{task_injected_tokens} tok via entry reduction]"
                if len(output) + len(footer) <= budget:
                    output += footer
        if task_fmt == "json" and isinstance(task_meta, dict):
            _record_recall_event(
                event_kind="recall",
                surface=task_meta.get("surface", "task_json"),
                mode=task_meta.get("mode", "auto"),
                raw_query=task_meta.get("raw_query", task_id),
                rewritten_query=task_meta.get("rewritten_query", task_id),
                task_id=task_meta.get("task_id", task_id),
                selected_entry_ids=task_meta.get("selected_entry_ids", []),
                hit_count=task_meta.get("hit_count", 0),
                output_chars=len(output),
            )
        _emit_knowledge_event_fail_open(
            "briefing_served",
            {
                "query": task_id,
                "surface": task_meta.get("surface", "task") if isinstance(task_meta, dict) else "task",
                "mode": task_meta.get("mode", "auto") if isinstance(task_meta, dict) else "auto",
                "hit_count": task_meta.get("hit_count", 0) if isinstance(task_meta, dict) else 0,
                "output_chars": len(output),
            },
        )
        print(output)
        return

    # Handle --feedback <query> <good|bad> — query-level briefing feedback
    if "--feedback" in args:
        idx = args.index("--feedback")
        if idx + 2 < len(args):
            fb_query = args[idx + 1]
            fb_verdict = args[idx + 2]
            write_feedback_query(fb_query, fb_verdict)
        else:
            print("Error: --feedback requires <query> <good|bad>")
        return

    # Handle --wing/--room search
    wing_filter = ""
    room_filter = ""
    if "--wing" in args:
        idx = args.index("--wing")
        wing_filter = args[idx + 1] if idx + 1 < len(args) else ""
    if "--room" in args:
        idx = args.index("--room")
        room_filter = args[idx + 1] if idx + 1 < len(args) else ""

    if wing_filter or room_filter:
        limit = 10
        if "--limit" in args:
            idx = args.index("--limit")
            limit = int(args[idx + 1]) if idx + 1 < len(args) else 10
        print(search_by_wing_room(wing=wing_filter, room=room_filter, limit=limit))
        return

    # Parse arguments
    fmt = "md"
    limit = 3
    auto_mode = "--auto" in args
    full_mode = "--full" in args
    mode = "auto"
    mode_explicit = False

    if "--format" in args:
        idx = args.index("--format")
        fmt = args[idx + 1] if idx + 1 < len(args) else "md"

    if "--json" in args:
        fmt = "json"

    if "--compact" in args:
        fmt = "compact"

    if "--pack" in args:
        fmt = "pack"

    if "--mode" in args:
        idx = args.index("--mode")
        mode = args[idx + 1].lower() if idx + 1 < len(args) else "auto"
        mode_explicit = True

    if "--limit" in args:
        idx = args.index("--limit")
        limit = int(args[idx + 1]) if idx + 1 < len(args) else 3

    min_confidence = 0.5  # Default: filter out low-quality entries
    if "--min-confidence" in args:
        idx = args.index("--min-confidence")
        min_confidence = float(args[idx + 1]) if idx + 1 < len(args) else 0.5
    if "--all" in args:
        min_confidence = 0.0  # Show everything including low-confidence

    # --since YYYY-MM-DD / --days N: restrict results to entries seen on/after a date
    since_date: str | None = None
    if "--since" in args:
        idx = args.index("--since")
        if idx + 1 < len(args) and not args[idx + 1].startswith("--"):
            since_date = args[idx + 1]
    if "--days" in args and since_date is None:
        idx = args.index("--days")
        try:
            n_days = int(args[idx + 1]) if idx + 1 < len(args) and not args[idx + 1].startswith("--") else 7
            since_date = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=n_days)).strftime(
                "%Y-%m-%d"
            )
        except (ValueError, IndexError):
            pass

    subagent_mode = "--for-subagent" in args
    no_danger = "--no-danger" in args

    # --with-code-context: append relevant code spans from code_index (issue #747)
    with_code_context = "--with-code-context" in args
    code_tokens = 1000
    if "--code-tokens" in args:
        idx = args.index("--code-tokens")
        if idx + 1 < len(args) and not args[idx + 1].startswith("--"):
            try:
                code_tokens = max(100, min(4000, int(args[idx + 1])))
            except ValueError:
                code_tokens = 1000

    # --pinned [N]: always include top-N P0 entries regardless of query (issue #708)
    pinned_n = 0
    if "--pinned" in args:
        idx = args.index("--pinned")
        if idx + 1 < len(args) and not args[idx + 1].startswith("--"):
            try:
                pinned_n = int(args[idx + 1])
            except ValueError:
                pinned_n = 3  # default to 3 P0 pinned entries
        else:
            pinned_n = 3

    # --no-repeat [=off]: session-scoped entry deduplication (issue #783).
    # Auto-enables when COPILOT_SESSION_ID is set; use --no-repeat=off to disable.
    no_repeat = "--no-repeat=off" not in args  # default: auto-ON when session ID available
    if "--no-repeat=off" in args:
        no_repeat = False
    _no_repeat_session_id = os.environ.get("COPILOT_SESSION_ID", "")
    already_served: set[int] = set()
    if no_repeat and _no_repeat_session_id:
        already_served = _load_briefed_ids(_no_repeat_session_id)

    # --no-dedup: disable semantic near-duplicate collapse (issue #851).
    no_dedup = "--no-dedup" in args

    if auto_mode:
        query = auto_detect_context()
        print(f"[briefing] auto-detected: {query}", file=sys.stderr)
    else:
        # Filter out values that follow flags (including --budget, --available-tokens) by argument
        # position, so query terms matching those values are preserved.
        consumed_value_indices = set()
        for i, a in enumerate(args):
            if a in (
                "--format",
                "--limit",
                "--min-confidence",
                "--budget",
                "--mode",
                "--available-tokens",
                "--agent-tag",
                "--msg-tag",
                "--since",
                "--days",
                "--code-tokens",
            ) and i + 1 < len(args):
                consumed_value_indices.add(i + 1)
        query_parts = [
            a
            for i, a in enumerate(args)
            if i not in consumed_value_indices
            and not a.startswith("--")
            and a not in ("md", "json", "compact", "pack", str(limit))
        ]
        query = " ".join(query_parts)

    if not query:
        print("Error: Provide a task description or use --auto")
        return

    # Memory budget: cap output to N chars (issue #125 dynamic-budget path).
    # --budget N overrides everything (explicit caller cap).
    # --available-tokens N enables dynamic sizing: min(2000, N * 0.05).
    # No flags → budget=0 (no cap, existing behaviour preserved).
    explicit_budget = 0
    if "--budget" in args:
        idx = args.index("--budget")
        if idx + 1 < len(args) and not args[idx + 1].startswith("--"):
            try:
                explicit_budget = int(args[idx + 1])
            except ValueError:
                explicit_budget = 3000  # default on non-numeric value
        else:
            explicit_budget = 3000
    available_tokens = 0
    if "--available-tokens" in args:
        idx = args.index("--available-tokens")
        if idx + 1 < len(args) and not args[idx + 1].startswith("--"):
            try:
                available_tokens = int(args[idx + 1])
            except ValueError:
                available_tokens = 0

    # --pressure-compact: auto-compact when context window < SK_PRESSURE_THRESHOLD remaining
    if "--pressure-compact" in args and available_tokens > 0:
        if _check_pressure_compact(available_tokens):
            db_path_str = str(TOOLS_DIR / "knowledge.db")
            _run_pressure_compact(db_path_str)
            return

    budget = _compute_dynamic_budget(explicit_budget, available_tokens)

    # Auto-select output tier from BriefingBudget when --available-tokens is set
    # and no explicit tier flag was supplied (issue #772).
    if available_tokens > 0 and not explicit_budget:
        bb = BriefingBudget(total_available=available_tokens)
        if bb.output_tier == "full" and "--full" not in args:
            full_mode = True
        elif bb.output_tier == "titles" and "--compact" not in args and "--full" not in args:
            # Redirect to --titles-only mode for minimal budget
            print(generate_titles_only(query=query, limit=limit))
            return

    # Show budget allocation header in --full mode when --available-tokens is set.
    if full_mode and available_tokens > 0:
        print(BriefingBudget(total_available=available_tokens).describe())
        print()

    if subagent_mode:
        infer_auto_mode = mode_explicit
        output = generate_subagent_context(
            query, limit=limit, min_confidence=min_confidence, mode=mode, infer_auto_mode=infer_auto_mode
        )
        output_meta = None
    else:
        infer_auto_mode = mode_explicit or fmt == "pack"
        output, output_meta = generate_briefing(
            query,
            limit=limit,
            fmt=fmt,
            full=full_mode,
            min_confidence=min_confidence,
            mode=mode,
            infer_auto_mode=infer_auto_mode,
            with_meta=True,
            include_superseded="--include-superseded" in args,
            since_date=since_date,
            include_resolved="--include-resolved" in args,
            pinned_n=pinned_n,
            exclude_ids=already_served if (no_repeat and _no_repeat_session_id) else None,
            no_dedup=no_dedup,
        )

    if budget > 0 and len(output) > budget:
        # Token tracking (issue #125): measure injected size before committing output.
        # _estimate_tokens converts chars to approximate token count (ceil(chars/4)).
        # Priority order for degradation: mistakes + blast_radius (highest) stay visible
        # longest; patterns/decisions/tools degrade next; past_work/file_index last.
        # This ordering is already encoded in _format_compact and generate_briefing's
        # category ordering — progressive limit reduction respects it automatically.
        injected_tokens = _estimate_tokens(len(output))
        budget_tokens = _estimate_tokens(budget)
        # Smart budget: re-generate with progressively fewer entries until it fits.
        # This ensures we keep COMPLETE entries (not half-cut ones) and the most
        # relevant entries are preserved (search is ordered by confidence + relevance).
        for reduced_limit in range(max(1, limit - 1), 0, -1):
            if subagent_mode:
                output = generate_subagent_context(
                    query,
                    limit=reduced_limit,
                    min_confidence=min_confidence,
                    mode=mode,
                    infer_auto_mode=infer_auto_mode,
                )
            else:
                output, output_meta = generate_briefing(
                    query,
                    limit=reduced_limit,
                    fmt=fmt,
                    full=full_mode,
                    min_confidence=min_confidence,
                    mode=mode,
                    infer_auto_mode=infer_auto_mode,
                    with_meta=True,
                    include_superseded="--include-superseded" in args,
                    since_date=since_date,
                    include_resolved="--include-resolved" in args,
                    pinned_n=pinned_n,
                    exclude_ids=already_served if (no_repeat and _no_repeat_session_id) else None,
                    no_dedup=no_dedup,
                )
            if len(output) <= budget:
                break

        # If still over budget after limit=1, truncate at last complete line.
        # JSON and subagent-context (XML-like) output are not truncated — that
        # would corrupt their structure.  Those formats must fit within budget
        # via the progressive-limit loop above.
        if len(output) > budget and fmt not in ("json", "pack") and not subagent_mode:
            footer = f"\n[BUDGET {budget} chars / ~{budget_tokens} tok — injected ~{injected_tokens} tok → hard-truncated to fit]"
            avail = budget - len(footer)
            body = output[: max(0, avail)].rsplit("\n", 1)[0] if avail > 0 else ""
            output = (body + footer)[:budget]
        elif fmt not in ("json", "pack") and not subagent_mode and injected_tokens > budget_tokens:
            footer = f"\n[BUDGET ~{budget_tokens} tok — reduced from ~{injected_tokens} tok via entry reduction]"
            if len(output) + len(footer) <= budget:
                output += footer

    if (not subagent_mode) and isinstance(output_meta, dict):
        _record_recall_event(
            event_kind="recall",
            surface=output_meta.get("surface", "standard"),
            mode=output_meta.get("mode", mode),
            raw_query=output_meta.get("raw_query", query),
            rewritten_query=output_meta.get("rewritten_query", query),
            task_id=output_meta.get("task_id", ""),
            selected_entry_ids=output_meta.get("selected_entry_ids", []),
            hit_count=output_meta.get("hit_count", 0),
            output_chars=len(output),
        )
        _emit_knowledge_event_fail_open(
            "briefing_served",
            {
                "query": output_meta.get("raw_query", query),
                "surface": output_meta.get("surface", "standard"),
                "mode": output_meta.get("mode", mode),
                "hit_count": output_meta.get("hit_count", 0),
                "output_chars": len(output),
            },
        )

    # TODO(issue #754): add focused coverage for pack/code-context budget interactions.
    if with_code_context and query and fmt != "json":
        snippets = _query_code_context(DB_PATH, query, token_budget=code_tokens)
        if snippets:
            if fmt == "pack":
                try:
                    pack_payload = json.loads(output)
                except json.JSONDecodeError:
                    pack_payload = None
                if isinstance(pack_payload, dict):
                    selected_snippets = []
                    for snippet in snippets:
                        candidate_snippets = selected_snippets + [snippet]
                        candidate_output = json.dumps(
                            {**pack_payload, "code_context": candidate_snippets},
                            indent=2,
                            ensure_ascii=False,
                        )
                        if budget and len(candidate_output) > budget:
                            break
                        selected_snippets = candidate_snippets
                    if selected_snippets:
                        output = json.dumps(
                            {**pack_payload, "code_context": selected_snippets},
                            indent=2,
                            ensure_ascii=False,
                        )
            else:
                code_section = _format_code_context(snippets)
                if code_section:
                    if budget:
                        remaining = max(0, budget - len(output))
                        if remaining <= 100:
                            code_section = ""
                        elif len(code_section) > remaining:
                            code_section = code_section[:remaining].rsplit("\n", 1)[0]
                    if code_section:
                        output = output + code_section

    # Danger lane: prepend ⚠️ DANGER section for compact/pack/wakeup/agent modes
    # when --no-danger is not set (issue #781).
    if not no_danger and fmt in ("compact", "pack", "md") and DB_PATH.exists():
        try:
            _dl_db = sqlite3.connect(str(DB_PATH))
            _dl_db.row_factory = sqlite3.Row
            danger = _fetch_danger_lane(_dl_db, since_date=since_date)
            if danger:
                print(danger)
                print()
        except Exception:
            pass

    # Save served entry IDs to the session-briefed cache (issue #783 --no-repeat).
    if no_repeat and _no_repeat_session_id and isinstance(output_meta, dict):
        _save_briefed_ids(
            _no_repeat_session_id,
            already_served | set(output_meta.get("selected_entry_ids", [])),
        )

    print(output)


if __name__ == "__main__":
    main()
