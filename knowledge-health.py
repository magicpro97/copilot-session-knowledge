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
    python knowledge-health.py --recall       # Recall telemetry dashboard
    python knowledge-health.py --recall --json  # Recall telemetry as JSON
    python knowledge-health.py --sync         # Sync runtime dashboard
    python knowledge-health.py --sync --json  # Sync runtime as JSON
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


def compute_recall_stats() -> dict:
    """Compute lean recall telemetry aggregates."""
    db = get_db()
    try:
        table_exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='recall_events'"
        ).fetchone()
        if not table_exists:
            return {
                "available": False,
                "total_events": 0,
                "events_by_surface": [],
                "avg_output_by_surface_mode": [],
                "top_no_hit_queries": [],
                "top_repeated_detail_opens": [],
            }

        total_events = db.execute("SELECT COUNT(*) FROM recall_events").fetchone()[0]
        if total_events == 0:
            return {
                "available": True,
                "total_events": 0,
                "events_by_surface": [],
                "avg_output_by_surface_mode": [],
                "top_no_hit_queries": [],
                "top_repeated_detail_opens": [],
            }

        events_by_surface = [
            dict(row) for row in db.execute(
                """
                SELECT tool, surface, COUNT(*) AS event_count
                FROM recall_events
                GROUP BY tool, surface
                ORDER BY event_count DESC, tool ASC, surface ASC
                """
            ).fetchall()
        ]
        avg_output = [
            dict(row) for row in db.execute(
                """
                SELECT tool, surface, mode,
                       AVG(output_chars) AS avg_output_chars,
                       AVG(output_est_tokens) AS avg_output_est_tokens,
                       COUNT(*) AS event_count
                FROM recall_events
                GROUP BY tool, surface, mode
                ORDER BY event_count DESC, tool ASC, surface ASC, mode ASC
                """
            ).fetchall()
        ]
        no_hit_queries = [
            dict(row) for row in db.execute(
                """
                SELECT rewritten_query, COUNT(*) AS event_count
                FROM recall_events
                WHERE event_kind = 'recall'
                  AND hit_count = 0
                  AND COALESCE(rewritten_query, '') != ''
                GROUP BY rewritten_query
                ORDER BY event_count DESC, rewritten_query ASC
                LIMIT 10
                """
            ).fetchall()
        ]
        repeated_detail = [
            dict(row) for row in db.execute(
                """
                SELECT opened_entry_id, COUNT(*) AS open_count
                FROM recall_events
                WHERE event_kind = 'detail_open'
                  AND opened_entry_id IS NOT NULL
                GROUP BY opened_entry_id
                ORDER BY open_count DESC, opened_entry_id ASC
                LIMIT 10
                """
            ).fetchall()
        ]
        return {
            "available": True,
            "total_events": total_events,
            "events_by_surface": events_by_surface,
            "avg_output_by_surface_mode": avg_output,
            "top_no_hit_queries": no_hit_queries,
            "top_repeated_detail_opens": repeated_detail,
        }
    finally:
        db.close()


def compute_sync_stats() -> dict:
    """Compute sync runtime diagnostics for local-first status surfaces."""
    db = get_db()
    try:
        has_sync_state = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_state'"
        ).fetchone()
        has_sync_txns = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_txns'"
        ).fetchone()
        has_sync_failures = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_failures'"
        ).fetchone()
        has_cursors = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_cursors'"
        ).fetchone()
        if not (has_sync_state and has_sync_txns and has_sync_failures and has_cursors):
            return {
                "available": False,
                "local_replica_id": "",
                "pending_txns": 0,
                "committed_txns": 0,
                "failed_txns": 0,
                "failure_count": 0,
                "last_failure": None,
                "last_pushed_txn_id": "",
                "last_pulled_txn_id": "",
                "cursor_txn_id": "",
            }

        state_rows = db.execute("SELECT key, value FROM sync_state").fetchall()
        state = {str(r["key"]): str(r["value"] or "") for r in state_rows}
        local_replica_id = state.get("local_replica_id", "")
        cursor = ""
        if local_replica_id:
            row = db.execute(
                "SELECT last_txn_id FROM sync_cursors WHERE replica_id = ?",
                (local_replica_id,),
            ).fetchone()
            cursor = str((row[0] if row else "") or "")

        pending = db.execute(
            "SELECT COUNT(*) FROM sync_txns WHERE status='pending'"
        ).fetchone()[0]
        committed = db.execute(
            "SELECT COUNT(*) FROM sync_txns WHERE status='committed'"
        ).fetchone()[0]
        failed = db.execute(
            "SELECT COUNT(*) FROM sync_txns WHERE status='failed'"
        ).fetchone()[0]
        failure_count = db.execute("SELECT COUNT(*) FROM sync_failures").fetchone()[0]
        last_failure_row = db.execute(
            """
            SELECT failed_at, error_code, error_message
            FROM sync_failures
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        last_failure = dict(last_failure_row) if last_failure_row else None
        return {
            "available": True,
            "local_replica_id": local_replica_id,
            "pending_txns": pending,
            "committed_txns": committed,
            "failed_txns": failed,
            "failure_count": failure_count,
            "last_failure": last_failure,
            "last_pushed_txn_id": state.get("last_pushed_txn_id", ""),
            "last_pulled_txn_id": state.get("last_pulled_txn_id", ""),
            "cursor_txn_id": cursor,
            "last_push_at": state.get("last_push_at", ""),
            "last_pull_at": state.get("last_pull_at", ""),
            "last_error": state.get("last_error", ""),
        }
    finally:
        db.close()


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


def format_recall_report(stats: dict) -> str:
    """Format recall telemetry as a text dashboard."""
    if not stats.get("available"):
        return "Recall telemetry unavailable (recall_events table not found)."
    if stats.get("total_events", 0) == 0:
        return "Recall telemetry is empty (no events recorded yet)."

    lines = [
        "╔══════════════════════════════════════════╗",
        "║  📡 Recall Telemetry",
        "╚══════════════════════════════════════════╝",
        "",
        f"Total recall events: {stats['total_events']}",
        "",
        "By tool/surface:",
    ]
    for row in stats.get("events_by_surface", []):
        lines.append(f"  - {row['tool']}/{row['surface']}: {row['event_count']}")

    lines.append("")
    lines.append("Average output by tool/surface/mode:")
    for row in stats.get("avg_output_by_surface_mode", []):
        mode = row.get("mode") or "(none)"
        avg_chars = round(float(row.get("avg_output_chars", 0) or 0), 1)
        avg_tokens = round(float(row.get("avg_output_est_tokens", 0) or 0), 1)
        lines.append(
            f"  - {row['tool']}/{row['surface']}/{mode}: "
            f"{avg_chars} chars, {avg_tokens} tok avg ({row['event_count']} events)"
        )

    lines.append("")
    lines.append("Top no-hit queries:")
    no_hit = stats.get("top_no_hit_queries", [])
    if no_hit:
        for row in no_hit:
            lines.append(f"  - {row['rewritten_query']}: {row['event_count']}")
    else:
        lines.append("  - (none)")

    lines.append("")
    lines.append("Top repeated detail opens:")
    repeated = stats.get("top_repeated_detail_opens", [])
    if repeated:
        for row in repeated:
            lines.append(f"  - #{row['opened_entry_id']}: {row['open_count']}")
    else:
        lines.append("  - (none)")

    return "\n".join(lines)


def format_sync_report(stats: dict) -> str:
    """Format sync runtime diagnostics as a text dashboard."""
    if not stats.get("available"):
        return "Sync runtime unavailable (sync foundation tables not found)."

    lines = [
        "╔══════════════════════════════════════════╗",
        "║  🔁 Sync Runtime Status",
        "╚══════════════════════════════════════════╝",
        "",
        f"Replica:            {stats.get('local_replica_id') or '(unset)'}",
        f"Pending txns:       {stats.get('pending_txns', 0)}",
        f"Committed txns:     {stats.get('committed_txns', 0)}",
        f"Failed txns:        {stats.get('failed_txns', 0)}",
        f"Failure rows:       {stats.get('failure_count', 0)}",
        "",
        f"Last pushed txn:    {stats.get('last_pushed_txn_id') or '(none)'}",
        f"Last pulled txn:    {stats.get('last_pulled_txn_id') or '(none)'}",
        f"Cursor txn:         {stats.get('cursor_txn_id') or '(none)'}",
        f"Last push at:       {stats.get('last_push_at') or '(none)'}",
        f"Last pull at:       {stats.get('last_pull_at') or '(none)'}",
    ]
    if stats.get("last_error"):
        lines.extend(["", f"Last daemon error:   {stats['last_error']}"])
    if stats.get("last_failure"):
        lf = stats["last_failure"]
        lines.extend([
            "",
            "Most recent failure:",
            f"  {lf.get('failed_at', '')} {lf.get('error_code', '')} {lf.get('error_message', '')}".strip(),
        ])
    return "\n".join(lines)


def main():
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print(__doc__)
        return

    if "--recall" in args:
        recall_stats = compute_recall_stats()
        if "--json" in args:
            print(json.dumps(recall_stats, indent=2, ensure_ascii=False))
        else:
            print(format_recall_report(recall_stats))
        return

    if "--sync" in args:
        sync_stats = compute_sync_stats()
        if "--json" in args:
            print(json.dumps(sync_stats, indent=2, ensure_ascii=False))
        else:
            print(format_sync_report(sync_stats))
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
