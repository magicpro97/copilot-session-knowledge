#!/usr/bin/env python3
"""skill-suggest.py — Mine the session knowledge DB and propose new skill candidates.

Reads from ~/.copilot/session-state/knowledge.db (read-only).
Checks existing skills in the repo's skills/ directory to avoid duplicates.
Outputs SKILL.md draft content that passes validate-skill.py.

CONSERVATIVE: suggestion-only surface — does NOT create or deploy skills.

Usage:
    python skill-suggest.py
    python skill-suggest.py --min-occurrences 3
    python skill-suggest.py --format json
    python skill-suggest.py --min-occurrences 3 --format json
    python skill-suggest.py --skills-dir /path/to/skills
    python skill-suggest.py --db /path/to/knowledge.db
    python skill-suggest.py --limit 10
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SESSION_STATE = Path.home() / ".copilot" / "session-state"
DB_PATH = SESSION_STATE / "knowledge.db"
DEFAULT_SKILLS_DIR = Path(__file__).parent / "skills"

# Category-based score weight: higher = more valuable signal
_CATEGORY_WEIGHT: dict[str, float] = {
    "pattern": 2.0,
    "decision": 1.5,
    "discovery": 1.2,
    "tool": 1.0,
    "mistake": 1.0,
    "feature": 0.8,
    "refactor": 0.7,
}

_STOPWORDS = frozenset(
    {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "can", "it", "this", "that", "these",
        "those", "i", "we", "you", "he", "she", "they", "not", "no", "so",
        "if", "then", "when", "where", "what", "which", "who", "how", "all",
        "any", "each", "more", "most", "also", "just", "up", "out", "as",
        "into", "than", "their", "its", "our", "my", "your", "his", "her",
        "them", "us", "me", "after", "before", "during", "while", "since",
        "until", "too", "very", "about", "use", "used", "using", "run",
        "running", "make", "new", "only", "now", "time", "way", "need",
        "needs", "see", "get", "set", "add", "put", "let", "say", "one",
        "two", "per", "via", "etc", "yet", "got",
    }
)


def _slugify(text: str) -> str:
    """Convert arbitrary text to a valid Agent Skills name slug.

    Produces lowercase, alphanumeric-plus-hyphens, 1–64 chars,
    no leading/trailing/consecutive hyphens.
    """
    s = (text or "").lower().strip()
    # Replace non-alphanumeric runs with a single hyphen
    s = re.sub(r"[^a-z0-9]+", "-", s)
    # Collapse consecutive hyphens
    s = re.sub(r"-+", "-", s)
    # Strip leading/trailing hyphens
    s = s.strip("-")
    if not s:
        return "unnamed-skill"
    # Names must not start with a digit per spec
    if s[0].isdigit():
        s = "skill-" + s
    return s[:64].rstrip("-")


def _load_existing_skills(skills_dir: Path) -> list[dict]:
    """Load existing skill names from a skills directory.

    Returns a list of dicts with keys: name, path.
    """
    skills: list[dict] = []
    if not skills_dir.exists():
        return skills
    for skill_md in skills_dir.rglob("SKILL.md"):
        try:
            content = skill_md.read_text(encoding="utf-8", errors="replace")
            name: str = ""
            if content.startswith("---"):
                fm_end = content.find("---", 3)
                if fm_end != -1:
                    fm = content[3:fm_end]
                    m = re.search(
                        r"^name:\s*['\"]?([^\s'\"#\n]+)['\"]?",
                        fm,
                        re.MULTILINE,
                    )
                    if m:
                        name = m.group(1).strip()
            if not name:
                name = skill_md.parent.name
            skills.append({"name": name, "path": str(skill_md)})
        except Exception:
            skills.append({"name": skill_md.parent.name, "path": str(skill_md)})
    return skills


def _token_overlap(a: str, b: str) -> float:
    """Compute Jaccard similarity between token sets of two name strings."""
    ta = set(re.findall(r"[a-z0-9]+", a.lower()))
    tb = set(re.findall(r"[a-z0-9]+", b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _find_overlap(candidate_name: str, existing_skills: list[dict]) -> list[str]:
    """Find existing skills that significantly overlap with candidate_name."""
    overlaps: list[str] = []
    for skill in existing_skills:
        existing = skill["name"]
        if existing == candidate_name or _token_overlap(candidate_name, existing) >= 0.4:
            overlaps.append(existing)
    return overlaps


def _make_skill_draft(
    name: str,
    title: str,
    top_tags: list[str],
    sample_entries: list[dict],
) -> str:
    """Generate SKILL.md draft content that passes validate-skill.py.

    Guarantees:
    - YAML frontmatter with valid name and ≥10-word description containing
      trigger phrases ("use when", "triggers", "invoke")
    - A # heading (title)
    - A "## When to use" section (activates)
    - A "## Workflow" section
    - At least one <example>…</example> block
    - No security-pattern violations
    - Well under 500 lines
    """
    name_human = name.replace("-", " ")
    tag_str = ", ".join(top_tags[:5]) if top_tags else name_human

    # Description: ≥10 words, trigger phrases required by validator
    desc = (
        f"Use when working with {name_human}. "
        f"Triggers on: {tag_str}. "
        f"Invoke when you need guidance on {name_human} patterns, "
        f"workflows, or decisions from session history."
    )
    desc = re.sub(r"\s+", " ", desc).strip()[:300]

    # When-to-use bullet list
    if top_tags:
        when_bullets = "\n".join(f"- {t}" for t in top_tags[:5])
    else:
        when_bullets = f"- {name_human}"

    # Knowledge summary from sample entries
    content_lines: list[str] = []
    for entry in sample_entries[:3]:
        e_title = (entry.get("title") or "").strip()
        e_content = re.sub(r"\s+", " ", (entry.get("content") or "")[:120]).strip()
        if e_title:
            content_lines.append(f"- **{e_title}**: {e_content}")
    content_block = (
        "\n".join(content_lines)
        if content_lines
        else f"- Apply proven patterns for {name_human}."
    )

    # Example block from the first sample entry
    if sample_entries:
        ex_raw = (sample_entries[0].get("content") or "")[:100]
        ex_content = re.sub(r"\s+", " ", ex_raw).strip()
    else:
        ex_content = f"Apply {name_human} best practices."
    if not ex_content:
        ex_content = f"Relevant patterns and decisions surfaced from session history."

    lines = [
        "---",
        f"name: {name}",
        "description: >-",
        f"  {desc}",
        "---",
        "",
        f"# {title}",
        "",
        "## When to use",
        "",
        f"Use this skill when working on {name_human} tasks. Activates on:",
        "",
        when_bullets,
        "",
        "## Knowledge summary",
        "",
        "Recurring patterns and decisions from session history:",
        "",
        content_block,
        "",
        "## Workflow",
        "",
        f"1. Review relevant knowledge entries for {name_human} context.",
        "2. Apply the established patterns from session history.",
        "3. Validate the approach against known mistakes and decisions.",
        "4. Record new learnings: `sk learn --pattern` or `sk learn --decision`.",
        "",
        "<example>",
        f"**Scenario**: Working on a task involving {name_human}.",
        "",
        f"**Action**: Apply `{name}` skill to surface relevant session knowledge.",
        "",
        f"**Result**: {ex_content}",
        "</example>",
        "",
    ]
    return "\n".join(lines)


def _open_db_readonly(db_path: Path):
    """Open the knowledge DB in read-only mode. Returns None if unavailable."""
    if not db_path.exists():
        return None
    try:
        uri = "file:{}?mode=ro".format(db_path.as_posix())
        db = sqlite3.connect(uri, uri=True)
        db.row_factory = sqlite3.Row
        return db
    except Exception:
        try:
            db = sqlite3.connect(str(db_path))
            db.row_factory = sqlite3.Row
            return db
        except Exception:
            return None


def _table_exists(db: sqlite3.Connection, name: str) -> bool:
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


# Signal-type score weights for boosting improvement-signal-derived candidates.
_SIGNAL_TYPE_WEIGHT: dict[str, float] = {
    "missed_match": 2.0,    # Strong: no skill existed — high priority to create one
    "wrong_skill": 1.5,     # Medium: wrong skill fired — patch candidate
    "outdated_skill": 1.5,  # Medium: skill is stale — update candidate
}


def _load_improvement_signals(db_path: Path, limit: int = 100) -> list[dict]:
    """Load unconsumed improvement signals from the knowledge DB.

    Returns an empty list if the table does not exist or the DB is unavailable.
    Fail-open: any exception yields an empty list.
    """
    db = _open_db_readonly(db_path)
    if db is None:
        return []
    try:
        if not _table_exists(db, "improvement_signals"):
            return []
        rows = db.execute(
            """
            SELECT id, session_id, query, signal_type, mentioned_skill, created_at
            FROM improvement_signals
            WHERE consumed = 0
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        try:
            db.close()
        except Exception:
            pass


def _signals_to_candidates(signals: list[dict]) -> list[dict]:
    """Convert unconsumed improvement signals into skill candidates.

    Only signals with a non-empty `mentioned_skill` produce named candidates.
    Signals without a `mentioned_skill` are grouped by slugified query text as
    a lower-priority fallback.

    Returns a list of candidate dicts compatible with mine_patterns() output.
    """
    # --- Group by mentioned_skill slug first ---
    named: dict[str, list[dict]] = {}
    unnamed: dict[str, list[dict]] = {}

    for sig in signals:
        ms = (sig.get("mentioned_skill") or "").strip()
        if ms:
            key = _slugify(ms)
            if key and key != "unnamed-skill":
                named.setdefault(key, []).append(sig)
                continue
        # fallback: group by query slug
        qk = _slugify(sig.get("query") or "")
        if qk and qk != "unnamed-skill":
            unnamed.setdefault(qk, []).append(sig)

    candidates: list[dict] = []

    for slug, sigs in named.items():
        signal_types = list({s["signal_type"] for s in sigs})
        weight = max(_SIGNAL_TYPE_WEIGHT.get(st, 1.0) for st in signal_types)
        score = len(sigs) * weight

        rep = sigs[0]
        rep_title = (rep.get("mentioned_skill") or "").strip() or slug
        patch_note = ""
        if any(st in ("wrong_skill", "outdated_skill") for st in signal_types):
            patch_note = f"Signal suggests skill '{rep_title}' may need updating."

        candidates.append(
            {
                "candidate_name": slug,
                "source_cluster": rep.get("mentioned_skill") or slug,
                "cluster_type": "improvement_signal",
                "score": round(score, 2),
                "total_occurrences": len(sigs),
                "entry_count": len(sigs),
                "avg_confidence": 1.0,
                "category": "pattern",
                "top_tags": signal_types,
                "representative_title": rep_title,
                "sample_entries": [
                    {"title": s.get("query", ""), "content": s.get("query", ""), "tags": s["signal_type"]}
                    for s in sigs[:3]
                ],
                "signal_count": len(sigs),
                "signal_types": signal_types,
                "patch_guidance": patch_note,
            }
        )

    for slug, sigs in unnamed.items():
        signal_types = list({s["signal_type"] for s in sigs})
        weight = max(_SIGNAL_TYPE_WEIGHT.get(st, 1.0) for st in signal_types) * 0.5  # lower weight, no named skill
        score = len(sigs) * weight

        rep = sigs[0]
        rep_query = (rep.get("query") or "").strip() or slug
        candidates.append(
            {
                "candidate_name": slug,
                "source_cluster": rep_query,
                "cluster_type": "improvement_signal_query",
                "score": round(score, 2),
                "total_occurrences": len(sigs),
                "entry_count": len(sigs),
                "avg_confidence": 0.7,
                "category": "pattern",
                "top_tags": signal_types,
                "representative_title": rep_query,
                "sample_entries": [
                    {"title": s.get("query", ""), "content": s.get("query", ""), "tags": s["signal_type"]}
                    for s in sigs[:3]
                ],
                "signal_count": len(sigs),
                "signal_types": signal_types,
                "patch_guidance": "",
            }
        )

    return candidates


def mine_patterns(
    db_path: Path,
    min_occurrences: int = 3,
    limit: int = 20,
    categories: list[str] | None = None,
) -> list[dict]:
    """Mine the knowledge DB for repeating skill-worthy clusters.

    Returns a list of candidate dicts sorted by score descending.
    Each dict has keys: candidate_name, source_cluster, cluster_type,
    score, total_occurrences, entry_count, avg_confidence, category,
    top_tags, representative_title, sample_entries.
    """
    if categories is None:
        categories = ["pattern", "decision", "discovery", "tool", "mistake"]

    db = _open_db_readonly(db_path)
    if db is None:
        return []

    candidates: list[dict] = []

    try:
        if not _table_exists(db, "knowledge_entries"):
            return []

        placeholders = ",".join("?" * len(categories))

        # ── Strategy 1: cluster by topic_key ──────────────────────────────
        rows = db.execute(
            f"""
            SELECT
                topic_key,
                category,
                GROUP_CONCAT(title, '|||')                  AS all_titles,
                GROUP_CONCAT(COALESCE(tags, ''), ',')       AS all_tags,
                GROUP_CONCAT(content, '|||')                AS all_contents,
                SUM(COALESCE(occurrence_count, 1))          AS total_occurrences,
                AVG(COALESCE(confidence, 1.0))              AS avg_confidence,
                COUNT(*)                                    AS entry_count
            FROM knowledge_entries
            WHERE topic_key IS NOT NULL AND topic_key != ''
              AND category IN ({placeholders})
            GROUP BY topic_key
            HAVING total_occurrences >= ?
            ORDER BY total_occurrences DESC, avg_confidence DESC
            LIMIT ?
            """,
            (*categories, min_occurrences, limit),
        ).fetchall()

        for row in rows:
            topic_key = row["topic_key"]
            all_titles = (row["all_titles"] or "").split("|||")
            raw_tags = (row["all_tags"] or "").split(",")
            all_tags = [t.strip().lower() for t in raw_tags if t.strip()]

            rep_title = all_titles[0].strip() if all_titles else topic_key

            tag_freq: dict[str, int] = {}
            for t in all_tags:
                if t and len(t) >= 3 and t not in _STOPWORDS:
                    tag_freq[t] = tag_freq.get(t, 0) + 1
            top_tags = [k for k, _ in sorted(tag_freq.items(), key=lambda x: -x[1])[:8]]

            cat = row["category"] or "pattern"
            weight = _CATEGORY_WEIGHT.get(cat, 1.0)
            score = float(row["total_occurrences"]) * weight

            samples_q = db.execute(
                f"""
                SELECT title, content, tags
                FROM knowledge_entries
                WHERE topic_key = ? AND category IN ({placeholders})
                ORDER BY COALESCE(occurrence_count, 1) DESC
                LIMIT 3
                """,
                (topic_key, *categories),
            ).fetchall()

            name = _slugify(topic_key)
            if not name or name == "unnamed-skill":
                name = _slugify(rep_title)

            candidates.append(
                {
                    "candidate_name": name,
                    "source_cluster": topic_key,
                    "cluster_type": "topic_key",
                    "score": round(score, 2),
                    "total_occurrences": int(row["total_occurrences"]),
                    "entry_count": int(row["entry_count"]),
                    "avg_confidence": round(float(row["avg_confidence"]), 3),
                    "category": cat,
                    "top_tags": top_tags,
                    "representative_title": rep_title,
                    "sample_entries": [dict(r) for r in samples_q],
                }
            )

        # ── Strategy 2: cluster by tag for entries without topic_key ───────
        tag_rows = db.execute(
            f"""
            SELECT tags, category, title, content,
                   COALESCE(occurrence_count, 1) AS occurrence_count,
                   COALESCE(confidence, 1.0)     AS confidence
            FROM knowledge_entries
            WHERE (topic_key IS NULL OR topic_key = '')
              AND tags IS NOT NULL AND tags != ''
              AND category IN ({placeholders})
            ORDER BY occurrence_count DESC
            LIMIT 500
            """,
            (*categories,),
        ).fetchall()

        tag_buckets: dict[str, list[dict]] = {}
        for row in tag_rows:
            for raw_tag in (row["tags"] or "").split(","):
                t = raw_tag.strip().lower()
                if t and len(t) >= 3 and t not in _STOPWORDS:
                    if t not in tag_buckets:
                        tag_buckets[t] = []
                    tag_buckets[t].append(
                        {
                            "title": row["title"],
                            "content": row["content"],
                            "tags": row["tags"],
                            "occurrence_count": int(row["occurrence_count"]),
                            "confidence": float(row["confidence"]),
                            "category": row["category"],
                        }
                    )

        existing_names = {c["candidate_name"] for c in candidates}

        for tag, entries in sorted(
            tag_buckets.items(),
            key=lambda kv: -sum(e["occurrence_count"] for e in kv[1]),
        ):
            total_occ = sum(e["occurrence_count"] for e in entries)
            if total_occ < min_occurrences:
                continue

            name = _slugify(tag)
            if name in existing_names:
                continue

            avg_conf = sum(e["confidence"] for e in entries) / len(entries)
            cat_counts: dict[str, int] = {}
            for e in entries:
                c = e.get("category") or "pattern"
                cat_counts[c] = cat_counts.get(c, 0) + e["occurrence_count"]
            cat = max(cat_counts, key=lambda k: cat_counts[k])

            weight = _CATEGORY_WEIGHT.get(cat, 1.0)
            score = total_occ * weight

            rep = max(entries, key=lambda e: e["occurrence_count"])
            rep_title = rep["title"]

            all_tags_freq: dict[str, int] = {}
            for e in entries:
                for t2 in (e.get("tags") or "").split(","):
                    t2 = t2.strip().lower()
                    if t2 and len(t2) >= 3 and t2 not in _STOPWORDS:
                        all_tags_freq[t2] = all_tags_freq.get(t2, 0) + 1
            top_tags = [k for k, _ in sorted(all_tags_freq.items(), key=lambda x: -x[1])[:8]]

            sample_entries = [
                {"title": e["title"], "content": e["content"], "tags": e["tags"]}
                for e in sorted(entries, key=lambda e: -e["occurrence_count"])[:3]
            ]

            existing_names.add(name)
            candidates.append(
                {
                    "candidate_name": name,
                    "source_cluster": tag,
                    "cluster_type": "tag",
                    "score": round(score, 2),
                    "total_occurrences": total_occ,
                    "entry_count": len(entries),
                    "avg_confidence": round(avg_conf, 3),
                    "category": cat,
                    "top_tags": top_tags,
                    "representative_title": rep_title,
                    "sample_entries": sample_entries,
                }
            )

            if len(candidates) >= limit * 2:
                break

    except sqlite3.Error:
        pass
    finally:
        try:
            db.close()
        except Exception:
            pass

    candidates.sort(key=lambda c: -c["score"])
    return candidates[:limit]


def suggest(
    db_path: Path | None = None,
    skills_dir: Path | None = None,
    min_occurrences: int = 3,
    limit: int = 20,
) -> dict:
    """Run the full skill suggestion pipeline.

    Returns a result dict suitable for JSON serialisation.
    Does NOT write any files or modify any state.

    Signal integration (fail-open):
    - Loads unconsumed improvement_signals rows if the table exists.
    - Signal-derived candidates are merged with knowledge-derived candidates.
    - If a signal candidate name already exists from knowledge mining, its score
      is boosted rather than duplicated.
    - If the improvement_signals table is absent the output is identical to
      the pre-signal behavior.
    """
    if db_path is None:
        db_path = DB_PATH
    if skills_dir is None:
        skills_dir = DEFAULT_SKILLS_DIR

    now = datetime.now(timezone.utc).isoformat()

    existing_skills = _load_existing_skills(skills_dir)
    existing_skill_names = [s["name"] for s in existing_skills]

    raw_candidates = mine_patterns(db_path, min_occurrences=min_occurrences, limit=limit * 2)

    # Load and merge unconsumed improvement signals (fail-open).
    try:
        raw_signals = _load_improvement_signals(db_path, limit=200)
        signal_candidates = _signals_to_candidates(raw_signals)
        signal_count = len(raw_signals)
    except Exception:
        raw_signals = []
        signal_candidates = []
        signal_count = 0

    # Build a lookup from name → index for knowledge candidates.
    name_to_idx: dict[str, int] = {}
    for i, c in enumerate(raw_candidates):
        name_to_idx[c["candidate_name"]] = i

    # Merge: boost existing candidate or append new signal candidate.
    for sc in signal_candidates:
        name = sc["candidate_name"]
        if name in name_to_idx:
            # Boost existing knowledge candidate's score.
            existing = raw_candidates[name_to_idx[name]]
            existing["score"] = round(existing["score"] + sc["score"], 2)
            existing.setdefault("signal_count", 0)
            existing["signal_count"] = existing.get("signal_count", 0) + sc.get("signal_count", 0)
            existing.setdefault("signal_types", [])
            for st in sc.get("signal_types", []):
                if st not in existing["signal_types"]:
                    existing["signal_types"].append(st)
            if sc.get("patch_guidance"):
                existing["patch_guidance"] = sc["patch_guidance"]
        else:
            raw_candidates.append(sc)
            name_to_idx[name] = len(raw_candidates) - 1

    # Sort merged list by score descending.
    raw_candidates.sort(key=lambda c: -c["score"])

    suggestions: list[dict] = []
    seen_names: set[str] = set()

    for cand in raw_candidates:
        name = cand["candidate_name"]
        if name in seen_names:
            continue
        seen_names.add(name)

        overlaps = _find_overlap(name, existing_skills)
        draft = _make_skill_draft(
            name=name,
            title=cand["representative_title"],
            top_tags=cand["top_tags"],
            sample_entries=cand["sample_entries"],
        )

        sug_entry = {
            "candidate_name": name,
            "source_cluster": cand["source_cluster"],
            "cluster_type": cand["cluster_type"],
            "score": cand["score"],
            "total_occurrences": cand["total_occurrences"],
            "entry_count": cand["entry_count"],
            "avg_confidence": cand["avg_confidence"],
            "top_tags": cand["top_tags"],
            "overlap_with_existing": overlaps,
            "skill_draft": draft,
        }
        if "signal_count" in cand:
            sug_entry["signal_count"] = cand["signal_count"]
        if "signal_types" in cand:
            sug_entry["signal_types"] = cand["signal_types"]
        if cand.get("patch_guidance"):
            sug_entry["patch_guidance"] = cand["patch_guidance"]

        suggestions.append(sug_entry)

        if len(suggestions) >= limit:
            break

    return {
        "generated_at": now,
        "min_occurrences": min_occurrences,
        "db_path": str(db_path),
        "db_exists": db_path.exists(),
        "skills_dir": str(skills_dir),
        "existing_skills_checked": existing_skill_names,
        "suggestion_count": len(suggestions),
        "improvement_signal_count": signal_count,
        "suggestions": suggestions,
    }


def _print_text(result: dict) -> None:
    """Print human-readable output."""
    print("\n\U0001f50d Skill Suggestion Report")
    print(f"   DB: {result['db_path']} (exists: {result['db_exists']})")
    print(f"   Min occurrences: {result['min_occurrences']}")
    print(f"   Existing skills checked: {len(result['existing_skills_checked'])}")
    if result.get("improvement_signal_count", 0):
        print(f"   Improvement signals (unconsumed): {result['improvement_signal_count']}")
    print(f"   Suggestions found: {result['suggestion_count']}")
    print()

    if not result["suggestions"]:
        print("No skill candidates found above the threshold.")
        if not result["db_exists"]:
            print(f"  -> Knowledge DB not found at {result['db_path']}")
            print("  -> Run `sk watch` or `sk index build` to populate the DB first.")
        return

    for i, sug in enumerate(result["suggestions"], 1):
        overlaps = sug["overlap_with_existing"]
        print(f"{'=' * 60}")
        print(f"  Suggestion #{i}: {sug['candidate_name']}")
        print(
            f"  Score: {sug['score']} | Occurrences: {sug['total_occurrences']}"
            f" | Entries: {sug['entry_count']}"
        )
        print(f"  Source: {sug['source_cluster']} ({sug['cluster_type']})")
        if sug["top_tags"]:
            print(f"  Tags: {', '.join(sug['top_tags'][:5])}")
        if sug.get("signal_count"):
            print(f"  \U0001f4e1 Improvement signals: {sug['signal_count']} ({', '.join(sug.get('signal_types', []))})")
        if sug.get("patch_guidance"):
            print(f"  \U0001f527 {sug['patch_guidance']}")
        if overlaps:
            print(f"  \u26a0\ufe0f  Overlap with existing skills: {', '.join(overlaps)}")
        else:
            print("  \u2705 No overlap with existing skills")
        print()
        print("--- SKILL.md draft ---")
        print(sug["skill_draft"])
        print(f"{'=' * 60}")
        print()

    print(f"\u2705 {result['suggestion_count']} skill suggestion(s) generated.")
    print("To validate a draft: python validate-skill.py <path/to/SKILL.md>")
    print("Note: This tool suggests only -- it does NOT create or deploy skills.")


def main(argv: list[str] | None = None) -> int:
    """Entry point for sk skill-suggest."""
    parser = argparse.ArgumentParser(
        description="Mine knowledge DB and propose new skill candidates (suggestion-only).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python skill-suggest.py
  python skill-suggest.py --min-occurrences 3 --format json
  python skill-suggest.py --db ~/.copilot/session-state/knowledge.db
  python skill-suggest.py --skills-dir ./skills --limit 5
        """,
    )
    parser.add_argument(
        "--min-occurrences",
        type=int,
        default=3,
        metavar="N",
        help="Minimum occurrence threshold for a topic to become a candidate (default: 3)",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        dest="output_format",
        help="Output format: text (default) or json",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        metavar="PATH",
        help=f"Path to knowledge.db (default: {DB_PATH})",
    )
    parser.add_argument(
        "--skills-dir",
        type=str,
        default=None,
        metavar="PATH",
        help=f"Skills directory for dedup check (default: {DEFAULT_SKILLS_DIR})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        metavar="N",
        help="Maximum number of suggestions to return (default: 20)",
    )

    args = parser.parse_args(argv)

    db_path = Path(args.db).expanduser() if args.db else DB_PATH
    skills_dir = (
        Path(args.skills_dir).expanduser() if args.skills_dir else DEFAULT_SKILLS_DIR
    )

    result = suggest(
        db_path=db_path,
        skills_dir=skills_dir,
        min_occurrences=args.min_occurrences,
        limit=args.limit,
    )

    if args.output_format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        _print_text(result)

    return 0


if __name__ == "__main__":
    sys.exit(main())
