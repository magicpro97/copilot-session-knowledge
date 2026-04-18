#!/usr/bin/env python3
"""
knowledge-health.py — Knowledge base health score and diagnostics

Analyze the health of your knowledge base with actionable metrics.
Inspired by codeflow's health score concept.

Usage:
    python knowledge-health.py                # Full health report
    python knowledge-health.py --score        # Just the score (0-100)
    python knowledge-health.py --json         # JSON output
    python knowledge-health.py --stale 30     # Flag entries older than 30 days
"""

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

# Fix Windows console encoding
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SESSION_STATE = Path.home() / ".copilot" / "session-state"
DB_PATH = SESSION_STATE / "knowledge.db"


def get_db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        print("Error: Knowledge database not found.", file=sys.stderr)
        sys.exit(1)
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    return db


def compute_health(stale_days: int = 30) -> dict:
    """Compute comprehensive health metrics for the knowledge base."""
    db = get_db()

    # Total entries
    total = db.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
    if total == 0:
        db.close()
        return {"score": 0, "total": 0, "message": "Empty knowledge base"}

    # Category distribution
    cats = db.execute("""
        SELECT category, COUNT(*) as cnt
        FROM knowledge_entries GROUP BY category
    """).fetchall()
    cat_counts = {r["category"]: r["cnt"] for r in cats}

    # Categorization rate (entries with non-empty category)
    uncategorized = db.execute("""
        SELECT COUNT(*) FROM knowledge_entries
        WHERE category IS NULL OR category = ''
    """).fetchone()[0]
    categorized_pct = ((total - uncategorized) / total) * 100 if total > 0 else 0

    # Mistake:pattern ratio — indicates learning curve
    mistakes = cat_counts.get("mistake", 0)
    patterns = cat_counts.get("pattern", 0)
    if mistakes > 0 and patterns > 0:
        mp_ratio = patterns / mistakes
    elif patterns > 0:
        mp_ratio = float("inf")
    else:
        mp_ratio = 0.0

    # Staleness: entries older than stale_days
    cutoff = time.strftime("%Y-%m-%d", time.gmtime(time.time() - stale_days * 86400))
    stale = db.execute("""
        SELECT COUNT(*) FROM knowledge_entries
        WHERE last_seen < ? AND last_seen IS NOT NULL AND last_seen != ''
    """, (cutoff,)).fetchone()[0]
    stale_pct = (stale / total) * 100 if total > 0 else 0

    # Freshness: entries from last 7 days
    week_ago = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 7 * 86400))
    fresh = db.execute("""
        SELECT COUNT(*) FROM knowledge_entries
        WHERE first_seen >= ? AND first_seen IS NOT NULL
    """, (week_ago,)).fetchone()[0]

    # Knowledge relations
    relations = 0
    try:
        relations = db.execute("SELECT COUNT(*) FROM knowledge_relations").fetchone()[0]
    except sqlite3.OperationalError:
        pass

    entity_relations = 0
    try:
        entity_relations = db.execute("SELECT COUNT(*) FROM entity_relations").fetchone()[0]
    except sqlite3.OperationalError:
        pass

    relation_density = (relations + entity_relations) / total if total > 0 else 0

    # Embedding coverage
    embeddings = 0
    try:
        embeddings = db.execute("SELECT COUNT(DISTINCT source_id) FROM embeddings").fetchone()[0]
    except sqlite3.OperationalError:
        pass
    embed_pct = (embeddings / total) * 100 if total > 0 else 0

    # Confidence distribution
    high_conf = db.execute("""
        SELECT COUNT(*) FROM knowledge_entries WHERE confidence >= 0.8
    """).fetchone()[0]
    low_conf = db.execute("""
        SELECT COUNT(*) FROM knowledge_entries WHERE confidence < 0.5
    """).fetchone()[0]

    # Wing/room coverage
    wings = 0
    rooms = 0
    try:
        wings = db.execute("""
            SELECT COUNT(DISTINCT wing) FROM knowledge_entries
            WHERE wing IS NOT NULL AND wing != ''
        """).fetchone()[0]
        rooms = db.execute("""
            SELECT COUNT(DISTINCT room) FROM knowledge_entries
            WHERE room IS NOT NULL AND room != ''
        """).fetchone()[0]
    except sqlite3.OperationalError:
        pass

    # Sessions contributing knowledge
    sessions = db.execute("""
        SELECT COUNT(DISTINCT session_id) FROM knowledge_entries
        WHERE session_id IS NOT NULL AND session_id != ''
    """).fetchone()[0]

    db.close()

    # Compute composite score (0-100)
    scores = {
        "categorization": min(categorized_pct, 100) * 0.20,           # 20%
        "learning_curve": min(mp_ratio * 50, 100) * 0.20,             # 20% — higher ratio = better
        "freshness": min((fresh / max(total, 1)) * 500, 100) * 0.15,  # 15%
        "relation_density": min(relation_density * 100, 100) * 0.15,  # 15%
        "embedding_coverage": min(embed_pct, 100) * 0.15,             # 15%
        "confidence_quality": (high_conf / max(total, 1)) * 100 * 0.15,  # 15%
    }
    total_score = sum(scores.values())

    return {
        "score": round(total_score, 1),
        "total": total,
        "categories": cat_counts,
        "categorized_pct": round(categorized_pct, 1),
        "mistakes": mistakes,
        "patterns": patterns,
        "mp_ratio": round(mp_ratio, 2) if mp_ratio != float("inf") else "∞",
        "stale_count": stale,
        "stale_pct": round(stale_pct, 1),
        "stale_days": stale_days,
        "fresh_7d": fresh,
        "relations": relations,
        "entity_relations": entity_relations,
        "relation_density": round(relation_density, 2),
        "embeddings": embeddings,
        "embed_pct": round(embed_pct, 1),
        "high_confidence": high_conf,
        "low_confidence": low_conf,
        "wings": wings,
        "rooms": rooms,
        "sessions": sessions,
        "subscores": {k: round(v, 1) for k, v in scores.items()},
    }


def format_report(health: dict) -> str:
    """Format health metrics as a text dashboard."""
    score = health["score"]

    # Score emoji
    if score >= 80:
        grade, emoji = "Excellent", "🏆"
    elif score >= 60:
        grade, emoji = "Good", "✅"
    elif score >= 40:
        grade, emoji = "Fair", "🟡"
    else:
        grade, emoji = "Needs Work", "🔴"

    # Score bar
    filled = int(score / 5)
    bar = "█" * filled + "░" * (20 - filled)

    lines = [
        f"╔══════════════════════════════════════════╗",
        f"║  {emoji} Knowledge Health: {score}/100 ({grade})",
        f"║  [{bar}]",
        f"╚══════════════════════════════════════════╝",
        "",
        f"📊 Overview",
        f"  Total entries:     {health['total']:,}",
        f"  Sessions:          {health['sessions']:,}",
        f"  Categorized:       {health['categorized_pct']}%",
        f"  Fresh (7d):        {health['fresh_7d']} new entries",
        f"  Stale (>{health['stale_days']}d):      {health['stale_count']} ({health['stale_pct']}%)",
        "",
        f"📈 Learning Curve",
        f"  Mistakes:          {health['mistakes']:,}",
        f"  Patterns:          {health['patterns']:,}",
        f"  Pattern/Mistake:   {health['mp_ratio']}x",
    ]

    # Learning curve interpretation
    mp = health["mp_ratio"]
    if isinstance(mp, (int, float)) and mp >= 1.0:
        lines.append(f"  → ✅ Good: learning from mistakes")
    elif isinstance(mp, (int, float)) and mp > 0:
        lines.append(f"  → 🟡 Room to improve: more mistakes than patterns")
    else:
        lines.append(f"  → 🔴 No patterns extracted from mistakes yet")

    lines.extend([
        "",
        f"🔗 Knowledge Graph",
        f"  Relations:         {health['relations']:,}",
        f"  Entity relations:  {health['entity_relations']:,}",
        f"  Density:           {health['relation_density']} rel/entry",
        "",
        f"🧠 Embeddings",
        f"  Embedded:          {health['embeddings']:,} / {health['total']:,} ({health['embed_pct']}%)",
        "",
        f"🏗️ Organization",
        f"  Wings:             {health['wings']}",
        f"  Rooms:             {health['rooms']}",
        f"  High confidence:   {health['high_confidence']:,}",
        f"  Low confidence:    {health['low_confidence']:,}",
        "",
        f"📦 Category Breakdown",
    ])

    for cat, cnt in sorted(health["categories"].items(), key=lambda x: -x[1]):
        pct = (cnt / health["total"]) * 100
        bar_len = int(pct / 5)
        lines.append(f"  {cat:12s} {cnt:5,} {'▓' * bar_len}{'░' * (20 - bar_len)} {pct:.0f}%")

    # Subscores
    lines.extend(["", "📐 Subscores (weighted)"])
    for name, val in health["subscores"].items():
        max_val = {"categorization": 20, "learning_curve": 20, "freshness": 15,
                   "relation_density": 15, "embedding_coverage": 15,
                   "confidence_quality": 15}
        mx = max_val.get(name, 20)
        lines.append(f"  {name:25s} {val:5.1f}/{mx}")

    # Recommendations
    recs = []
    if health["categorized_pct"] < 90:
        recs.append("Run extract-knowledge.py to categorize uncategorized entries")
    if isinstance(mp, (int, float)) and mp < 0.5:
        recs.append("Review mistakes and extract patterns with learn.py --pattern")
    if health["embed_pct"] < 50:
        recs.append("Run embed.py --build to improve semantic search")
    if health["relation_density"] < 0.5:
        recs.append("Use learn.py --relate to connect related knowledge entries")
    if health["stale_pct"] > 50:
        recs.append("Review stale entries: query-session.py --mistakes --limit 20")

    if recs:
        lines.extend(["", "💡 Recommendations"])
        for i, r in enumerate(recs, 1):
            lines.append(f"  {i}. {r}")

    return "\n".join(lines)


def main():
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print(__doc__)
        return

    stale_days = 30
    if "--stale" in args:
        idx = args.index("--stale")
        stale_days = int(args[idx + 1]) if idx + 1 < len(args) else 30

    health = compute_health(stale_days=stale_days)

    if "--score" in args:
        print(health["score"])
    elif "--json" in args:
        print(json.dumps(health, indent=2, ensure_ascii=False))
    else:
        print(format_report(health))


if __name__ == "__main__":
    main()
