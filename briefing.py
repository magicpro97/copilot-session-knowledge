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
    python briefing.py "task desc" --budget 3000           # Cap output to 3000 chars (frozen snapshot)
    python briefing.py --task "memory-surface"              # Task-scoped recall for a task ID

Default output is compact (~500 tokens): titles + 1-line summaries with entry IDs.
Use --titles-only for ultra-compact index (~10 tokens/entry). Then --detail <id> for full.
Use --wakeup for ultra-compact AI wake-up context (~170 tokens).
Use --full for complete content with tags, confidence scores, and full text.
"""

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
DB_PATH = SESSION_STATE / "knowledge.db"

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


def get_db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        print("Error: Knowledge database not found. Run build-session-index.py first.", file=sys.stderr)
        sys.exit(1)
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    return db


def _safe_int_list(values) -> list[int]:
    out = []
    for value in values:
        try:
            iv = int(value)
        except (TypeError, ValueError):
            continue
        out.append(iv)
    return out


def _estimate_tokens(output_chars: int) -> int:
    return int(math.ceil(output_chars / 4)) if output_chars > 0 else 0


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
    """Feedback-aware reranking for knowledge entries."""
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
        return entries

    normalized_query = _normalize_feedback_query(query)
    if not normalized_query:
        return entries

    verdicts_by_id: dict[str, list[int]] = {}
    for r in rows:
        if _normalize_feedback_query(r["query"] or "") != normalized_query:
            continue
        rid = str(r["result_id"] or "")
        verdicts_by_id.setdefault(rid, []).append(int(r["verdict"]))

    if not verdicts_by_id:
        return entries

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

    def _bias_for(entry_id: str) -> float:
        votes = verdicts_by_id.get(entry_id, [])
        if not votes:
            return 0.0
        non_neutral = [v for v in votes if v != 0]
        if len(non_neutral) < 2:
            return 0.0
        feedback_sum = sum(non_neutral)
        return max(-0.15, min(0.15, feedback_sum * 0.05))

    ranked = []
    for idx, entry in enumerate(entries):
        base = normalized_scores[idx]
        bias = _bias_for(str(entry.get("id")))
        ranked.append((base + bias, idx, entry))

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


def search_knowledge_entries(
    db: sqlite3.Connection, query: str, category: str, limit: int = 3, min_confidence: float = 0.0
) -> list[dict]:
    """Search knowledge entries by category using FTS5 with adaptive strictness."""
    fts_query, strictness, confidence_delta = _build_adaptive_fts_query(query)
    effective_confidence = max(0.0, min(1.0, min_confidence + confidence_delta))

    results = []
    try:
        rows = db.execute(
            """
            SELECT ke.id, ke.title, ke.content, ke.tags,
                   ke.confidence, ke.session_id, ke.occurrence_count,
                   ke.document_id, ke.source_section,
                   ke.source_file, ke.start_line, ke.end_line,
                   ke.code_language, ke.code_snippet,
                   d.doc_type as source_doc_type,
                   d.title as source_doc_title,
                   d.file_path as source_doc_file_path,
                   d.seq as source_doc_seq
            FROM ke_fts fts
            JOIN knowledge_entries ke ON fts.rowid = ke.id
            LEFT JOIN documents d ON ke.document_id = d.id
            WHERE ke_fts MATCH ?
            AND ke.category = ?
            AND ke.confidence >= ?
            ORDER BY ke.confidence DESC, rank
            LIMIT ?
        """,
            (fts_query, category, effective_confidence, limit),
        ).fetchall()
        results.extend([dict(r) for r in rows])
    except sqlite3.OperationalError:
        try:
            rows = db.execute(
                """
                SELECT ke.id, ke.title, ke.content, ke.tags,
                       ke.confidence, ke.session_id, ke.occurrence_count
                FROM ke_fts fts
                JOIN knowledge_entries ke ON fts.rowid = ke.id
                WHERE ke_fts MATCH ?
                AND ke.category = ?
                AND ke.confidence >= ?
                ORDER BY ke.confidence DESC, rank
                LIMIT ?
            """,
                (fts_query, category, effective_confidence, limit),
            ).fetchall()
            results.extend([dict(r) for r in rows])
        except sqlite3.OperationalError:
            pass

    # Strict fallback: if exact-match returned nothing, retry with prefix query
    if not results and strictness == "strict":
        base_query = _sanitize_fts_query(query)
        try:
            rows = db.execute(
                """
                SELECT ke.id, ke.title, ke.content, ke.tags,
                       ke.confidence, ke.session_id, ke.occurrence_count,
                       ke.document_id, ke.source_section,
                       ke.source_file, ke.start_line, ke.end_line,
                       ke.code_language, ke.code_snippet,
                       d.doc_type as source_doc_type,
                       d.title as source_doc_title,
                       d.file_path as source_doc_file_path,
                       d.seq as source_doc_seq
                FROM ke_fts fts
                JOIN knowledge_entries ke ON fts.rowid = ke.id
                LEFT JOIN documents d ON ke.document_id = d.id
                WHERE ke_fts MATCH ?
                AND ke.category = ?
                AND ke.confidence >= ?
                ORDER BY ke.confidence DESC, rank
                LIMIT ?
            """,
                (base_query, category, min_confidence, limit),
            ).fetchall()
            results.extend([dict(r) for r in rows])
        except sqlite3.OperationalError:
            try:
                rows = db.execute(
                    """
                    SELECT ke.id, ke.title, ke.content, ke.tags,
                           ke.confidence, ke.session_id, ke.occurrence_count
                    FROM ke_fts fts
                    JOIN knowledge_entries ke ON fts.rowid = ke.id
                    WHERE ke_fts MATCH ?
                    AND ke.category = ?
                    AND ke.confidence >= ?
                    ORDER BY ke.confidence DESC, rank
                    LIMIT ?
                """,
                    (base_query, category, min_confidence, limit),
                ).fetchall()
                results.extend([dict(r) for r in rows])
            except sqlite3.OperationalError:
                pass

    return results


def search_semantic(
    db: sqlite3.Connection, query: str, category: str, limit: int = 3, min_confidence: float = 0.0
) -> list[dict]:
    """Search knowledge entries using vector embeddings."""
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
            for st, sid, score in vec_results:
                if score < 0.3:
                    continue
                row = db.execute(
                    """
                    SELECT id, title, content, tags, confidence,
                           session_id, occurrence_count, category
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
                    # Map TF-IDF section match to knowledge entries from the same session
                    ke_rows = db.execute(
                        """
                        SELECT ke.* FROM knowledge_entries ke
                        WHERE ke.category = ?
                          AND ke.session_id IN (
                              SELECT d.session_id FROM sections s
                              JOIN documents d ON s.document_id = d.id
                              WHERE s.id = ?
                          )
                        ORDER BY ke.confidence DESC
                        LIMIT ?
                    """,
                        (category, section_id, limit),
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

    for cat in categories:
        label = labels.get(cat, cat.upper())
        cat_limit = per_cat_limit.get(cat, limit)
        fts = search_knowledge_entries(db, rewritten_query, cat, cat_limit, min_confidence=min_confidence)
        sem = search_semantic(db, query, cat, cat_limit, min_confidence=min_confidence)
        # Merge and dedup by id
        seen = set()
        entries = []
        for e in fts + sem:
            eid = e[0] if isinstance(e, (list, tuple)) else e.get("id", id(e))
            if eid not in seen:
                seen.add(eid)
                entries.append(e)

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


def generate_briefing(
    query: str,
    limit: int = 3,
    fmt: str = "md",
    full: bool = False,
    min_confidence: float = 0.5,
    mode: str = "auto",
    infer_auto_mode: bool = True,
    with_meta: bool = False,
):
    """Generate a structured briefing from the knowledge base."""
    db = get_db()
    rewritten_query = _rewrite_query_local(query)
    active_mode, categories, per_cat_limit = _mode_category_config(limit, mode, query, infer_auto=infer_auto_mode)

    briefing_data = {}
    global_seen_titles = set()  # Cross-category dedup

    for cat in categories:
        # Combine FTS5 + semantic results, deduplicate
        cat_limit = per_cat_limit.get(cat, limit)
        fts_results = search_knowledge_entries(db, rewritten_query, cat, cat_limit, min_confidence=min_confidence)
        sem_results = search_semantic(db, query, cat, cat_limit, min_confidence=min_confidence)

        merged = []
        for r in fts_results + sem_results:
            title = r.get("title", "")
            if title not in global_seen_titles:
                global_seen_titles.add(title)
                merged.append(r)

        briefing_data[cat] = merged[:cat_limit]

    # Past related work
    past_work = search_past_work(db, rewritten_query, limit)

    # Blast radius analysis
    blast = blast_radius(db, rewritten_query)

    # Pack-only machine surface extras
    task_matches = []
    file_matches = []
    next_open = []
    if fmt == "pack":
        task_matches = _extract_task_matches(db, rewritten_query, limit=min(5, max(3, limit)))
        file_matches = _extract_file_matches(db, rewritten_query, limit=min(5, max(3, limit)))
        next_open = _extract_next_open(limit=5)

    db.close()

    selected_entry_ids = _safe_int_list(
        row.get("id") for rows in briefing_data.values() for row in rows if isinstance(row, dict)
    )

    # Check if we have anything
    total_entries = sum(len(v) for v in briefing_data.values()) + len(past_work)
    output = ""
    if total_entries == 0:
        if fmt == "json":
            output = json.dumps(
                {
                    "query": query,
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "sections": {},
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
            }
            output = json.dumps(pack, indent=2, ensure_ascii=False)
        else:
            output = f"No relevant past experience found for: {query}\n"
    else:
        # Format output
        if fmt == "json":
            output = _format_json(query, briefing_data, past_work, categories, blast)
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
            }
            output = json.dumps(pack, indent=2, ensure_ascii=False)
        elif fmt == "compact":
            output = _format_compact(query, briefing_data, past_work, categories, blast)
        elif full:
            output = _format_markdown(query, briefing_data, past_work, categories, blast)
        else:
            output = _format_default(query, briefing_data, past_work, categories, blast)

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


def _format_default(query: str, data: dict, past_work: list, categories: dict, blast: list = None) -> str:
    """Compact default format: titles + 1-line summaries (~500 tokens)."""
    lines = []
    lines.append(f"📋 Briefing: {query}")
    lines.append("")

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
            if summary:
                lines.append(f"  #{eid} {title} — {summary}")
            else:
                lines.append(f"  #{eid} {title}")
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

    total = sum(len(v) for v in data.values()) + len(past_work)
    lines.append(
        f"({total} entries) Use --full for complete content, or query-session.py --detail <id> for specific entry"
    )

    return "\n".join(lines)


def _format_markdown(query: str, data: dict, past_work: list, categories: dict, blast: list = None) -> str:
    """Format briefing as Markdown."""
    lines = []
    lines.append("# 📋 Pre-Task Briefing")
    lines.append("")
    lines.append(f"**Task**: {query}")
    lines.append(f"**Generated**: {time.strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    lines.append("---")
    lines.append("")

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

    lines.append("---")
    lines.append(
        f"_Briefing from knowledge.db — {sum(len(v) for v in data.values())} entries + {len(past_work)} past work refs_"
    )

    return "\n".join(lines)


def _format_json(query: str, data: dict, past_work: list, categories: dict, blast: list = None) -> str:
    """Format briefing as JSON."""
    output = {"query": query, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "sections": {}}

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


def _format_compact(query: str, data: dict, past_work: list, categories: dict, blast: list = None) -> str:
    """Compact format optimized for AI agent context injection.

    Minimal-first ordering: mistakes → blast_radius → patterns/decisions/tools → past_work.
    Mistakes and blast radius appear first so the most actionable risk context
    is visible in the smallest token budget.
    """
    lines = []
    safe_query = query[:100].replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
    lines.append(f'<briefing task="{safe_query}">\n')

    def _cat_block(cat: str) -> None:
        """Append one XML-style category block to *lines*."""
        entries = data.get(cat, [])
        if not entries:
            return
        lines.append(f"<{cat}s>")
        for entry in entries:
            title = entry.get("title", "")[:80]
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
            lines.append(f"- {title}: {first_line}")
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


def main():
    args = sys.argv[1:]

    if not args or "--help" in args or "-h" in args:
        print(__doc__)
        return

    # Handle --wakeup mode (ultra-compact, no query needed)
    if "--wakeup" in args:
        print(generate_wakeup())
        return

    # Handle --titles-only mode (progressive disclosure layer 1)
    if "--titles-only" in args:
        limit = 20
        if "--limit" in args:
            idx = args.index("--limit")
            limit = int(args[idx + 1]) if idx + 1 < len(args) else 20
        query_parts = [a for a in args if not a.startswith("--") and a != str(limit)]
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
        budget = 0
        if "--budget" in args:
            idx3 = args.index("--budget")
            try:
                budget = int(args[idx3 + 1]) if idx3 + 1 < len(args) else 3000
            except ValueError:
                budget = 3000
        task_meta = None
        if task_fmt == "json":
            output, task_meta = generate_task_briefing(task_id, limit=limit, fmt=task_fmt, with_meta=True)
        else:
            output = generate_task_briefing(task_id, limit=limit, fmt=task_fmt)
        if budget > 0 and len(output) > budget and task_fmt != "json":
            output = output[:budget].rsplit("\n", 1)[0]
            output += f"\n[BUDGET {budget} chars — showing highest-confidence entries only]"
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
        print(output)
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

    subagent_mode = "--for-subagent" in args

    if auto_mode:
        query = auto_detect_context()
        print(f"[briefing] auto-detected: {query}", file=sys.stderr)
    else:
        # Filter out values that follow flags (including --budget) by argument
        # position, so query terms matching those values are preserved.
        consumed_value_indices = set()
        for i, a in enumerate(args):
            if a in ("--format", "--limit", "--min-confidence", "--budget", "--mode") and i + 1 < len(args):
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

    # Memory budget: cap output to N chars (Hermes frozen snapshot pattern)
    # Budget-aware: reduce limit progressively to fit, rather than dumb truncation
    budget = 0
    if "--budget" in args:
        idx = args.index("--budget")
        if idx + 1 < len(args) and not args[idx + 1].startswith("--"):
            try:
                budget = int(args[idx + 1])
            except ValueError:
                budget = 3000  # default on non-numeric value
        else:
            budget = 3000

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
        )

    if budget > 0 and len(output) > budget:
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
                )
            if len(output) <= budget:
                break

        # If still over budget after limit=1, truncate at last complete line.
        # JSON and subagent-context (XML-like) output are not truncated — that
        # would corrupt their structure.  Those formats must fit within budget
        # via the progressive-limit loop above.
        if len(output) > budget and fmt not in ("json", "pack"):
            output = output[:budget].rsplit("\n", 1)[0]
            output += f"\n[BUDGET {budget} chars — showing highest-confidence entries only]"

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

    print(output)


if __name__ == "__main__":
    main()
