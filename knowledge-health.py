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
    python knowledge-health.py --insights     # Derived actionable insights dashboard
    python knowledge-health.py --insights --json  # Insights as JSON
"""

import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
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
    stale = db.execute(
        """
        SELECT COUNT(*) FROM knowledge_entries
        WHERE last_seen < ? AND last_seen IS NOT NULL AND last_seen != ''
    """,
        (cutoff,),
    ).fetchone()[0]
    stale_pct = (stale / total) * 100 if total > 0 else 0

    # Freshness: entries from last 7 days
    week_ago = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 7 * 86400))
    fresh = db.execute(
        """
        SELECT COUNT(*) FROM knowledge_entries
        WHERE first_seen >= ? AND first_seen IS NOT NULL
    """,
        (week_ago,),
    ).fetchone()[0]

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
        embedding_columns = {str(row["name"]) for row in db.execute("PRAGMA table_info(embeddings)").fetchall()}
        if "source_type" in embedding_columns:
            embeddings = db.execute(
                """
                SELECT COUNT(DISTINCT source_id)
                FROM embeddings
                WHERE source_type = 'knowledge'
                """
            ).fetchone()[0]
        else:
            embeddings = db.execute("SELECT COUNT(DISTINCT source_id) FROM embeddings").fetchone()[0]
    except sqlite3.OperationalError:
        pass
    embed_pct = min((embeddings / total) * 100, 100) if total > 0 else 0

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
        "categorization": min(categorized_pct, 100) * 0.20,  # 20%
        "learning_curve": min(mp_ratio * 50, 100) * 0.20,  # 20% — higher ratio = better
        "freshness": min((fresh / max(total, 1)) * 500, 100) * 0.15,  # 15%
        "relation_density": min(relation_density * 100, 100) * 0.15,  # 15%
        "embedding_coverage": min(embed_pct, 100) * 0.15,  # 15%
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
        table_exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='recall_events'").fetchone()
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
            dict(row)
            for row in db.execute(
                """
                SELECT tool, surface, COUNT(*) AS event_count
                FROM recall_events
                GROUP BY tool, surface
                ORDER BY event_count DESC, tool ASC, surface ASC
                """
            ).fetchall()
        ]
        avg_output = [
            dict(row)
            for row in db.execute(
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
            dict(row)
            for row in db.execute(
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
            dict(row)
            for row in db.execute(
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
        has_sync_state = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_state'").fetchone()
        has_sync_txns = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_txns'").fetchone()
        has_sync_failures = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_failures'"
        ).fetchone()
        has_cursors = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_cursors'").fetchone()
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

        pending = db.execute("SELECT COUNT(*) FROM sync_txns WHERE status='pending'").fetchone()[0]
        committed = db.execute("SELECT COUNT(*) FROM sync_txns WHERE status='committed'").fetchone()[0]
        failed = db.execute("SELECT COUNT(*) FROM sync_txns WHERE status='failed'").fetchone()[0]
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


_FILE_RE = re.compile(r"^[a-zA-Z0-9_./@+\-~]+$")


def _is_file_path(s: str) -> bool:
    """Return True only for strings that look like repo-local file paths (not prose)."""
    s = s.strip()
    if not s or len(s) > 150 or len(s) < 2:
        return False
    if " " in s or ":" in s or s.startswith("/") or s.startswith("\\"):
        return False
    normalized = s[2:] if s.startswith("./") else s
    if normalized.startswith("../") or normalized == "..":
        return False
    if "." not in normalized and "/" not in s:
        return False
    return bool(_FILE_RE.match(s))


def _compute_sync_advisory(
    total: int,
    mp_ratio,
    hot_files: list,
    health: dict,
) -> dict:
    """Return an advisory sync-contract signal for --insights.

    This function is purely read-only and advisory.  It does NOT alter
    compute_health() composite score, subscores, or any DB state.

    Returns a dict with:
      - ``status``: "ok" | "suggest" | "review"
      - ``reasons``: list of human-readable reason strings
      - ``checklist``: reference to docs/SYNC-MATRIX.md
    """
    reasons = []

    # Signal 1: many hot files but low pattern extraction (code churn without learning)
    if len(hot_files) >= 3 and isinstance(mp_ratio, (int, float)) and mp_ratio < 0.5 and health.get("mistakes", 0) >= 3:
        reasons.append(
            f"{len(hot_files)} hot file(s) with low pattern/mistake ratio ({mp_ratio:.2f}x) "
            "— consider extracting patterns from recent mistakes."
        )

    # Signal 2: knowledge base has many entries but no decisions recorded
    _cats = health.get("categories", {})
    decisions = int(_cats.get("decision", 0)) if isinstance(_cats, dict) else 0
    if total >= 10 and decisions == 0:
        reasons.append(
            "No decision entries recorded despite active knowledge base "
            "— consider documenting key architecture/approach decisions."
        )

    # Signal 3: high staleness with active hot files (docs/knowledge out of date)
    stale_pct = health.get("stale_pct", 0.0)
    if stale_pct >= 50 and len(hot_files) >= 2:
        reasons.append(
            f"{stale_pct:.0f}% of entries are stale while {len(hot_files)} hot file(s) are active "
            "— knowledge may be out of sync with recent code changes."
        )

    if not reasons:
        status = "ok"
    elif len(reasons) >= 2:
        status = "review"
    else:
        status = "suggest"

    return {
        "status": status,
        "reasons": reasons,
        "checklist": "docs/SYNC-MATRIX.md",
    }


def compute_insights(stale_days: int = 30) -> dict:
    """Derive actionable insights from the knowledge base."""
    health = compute_health(stale_days=stale_days)
    db = get_db()

    total = health.get("total", 0)
    high_conf = health.get("high_confidence", 0)
    low_conf = health.get("low_confidence", 0)
    stale_pct = health.get("stale_pct", 0.0)
    relation_density = health.get("relation_density", 0.0)
    embed_pct = health.get("embed_pct", 0.0)
    mp_ratio = health.get("mp_ratio", 0)
    high_conf_pct = round((high_conf / total) * 100, 1) if total else 0.0
    low_conf_pct = round((low_conf / total) * 100, 1) if total else 0.0

    overview = {
        "health_score": health.get("score", 0),
        "total_entries": total,
        "sessions": health.get("sessions", 0),
        "high_confidence_pct": high_conf_pct,
        "low_confidence_pct": low_conf_pct,
        "stale_pct": stale_pct,
        "relation_density": relation_density,
        "embedding_pct": embed_pct,
    }

    # ---- Quality alerts ----
    alerts = []

    if total == 0:
        alerts.append(
            {
                "id": "empty-db",
                "title": "Knowledge base is empty",
                "severity": "critical",
                "detail": "No entries found. Run build-session-index.py and extract-knowledge.py to populate it.",
            }
        )
    else:
        if low_conf_pct >= 50:
            alerts.append(
                {
                    "id": "low-confidence-dominant",
                    "title": f"{low_conf_pct:.0f}% of entries have low confidence (<0.5)",
                    "severity": "warning",
                    "detail": (
                        f"{low_conf} of {total} entries have confidence below 0.5. "
                        "This often indicates noisy automated extraction with weak signal."
                    ),
                }
            )
        elif low_conf_pct >= 25:
            alerts.append(
                {
                    "id": "low-confidence-elevated",
                    "title": f"{low_conf_pct:.0f}% of entries have low confidence (<0.5)",
                    "severity": "info",
                    "detail": f"{low_conf} entries below 0.5 confidence. Consider pruning weak entries.",
                }
            )

        if stale_pct >= 70:
            alerts.append(
                {
                    "id": "high-staleness",
                    "title": f"{stale_pct:.0f}% of entries are stale (>{stale_days}d)",
                    "severity": "critical",
                    "detail": f"Most entries haven't been seen in over {stale_days} days. The knowledge base may be stale.",
                }
            )
        elif stale_pct >= 40:
            alerts.append(
                {
                    "id": "moderate-staleness",
                    "title": f"{stale_pct:.0f}% of entries are stale (>{stale_days}d)",
                    "severity": "warning",
                    "detail": f"Many entries are over {stale_days} days old. Review for continued relevance.",
                }
            )

        if relation_density < 0.1 and total >= 10:
            alerts.append(
                {
                    "id": "sparse-relations",
                    "title": "Knowledge graph is sparse",
                    "severity": "warning",
                    "detail": (
                        f"Only {relation_density:.2f} relations per entry. "
                        "Entries are isolated islands; semantic connections are missing."
                    ),
                }
            )

        if embed_pct < 10 and total >= 5:
            alerts.append(
                {
                    "id": "no-embeddings",
                    "title": "Semantic search unavailable (embeddings missing)",
                    "severity": "warning" if total >= 20 else "info",
                    "detail": (
                        f"Only {embed_pct:.0f}% of entries have embeddings. Keyword-only search has poor recall."
                    ),
                }
            )

        if isinstance(mp_ratio, (int, float)) and mp_ratio < 0.3 and health.get("mistakes", 0) >= 5:
            alerts.append(
                {
                    "id": "low-pattern-extraction",
                    "title": "Few patterns extracted from mistakes",
                    "severity": "info",
                    "detail": (
                        f"Pattern/mistake ratio is {mp_ratio:.2f}x. "
                        "Mistakes are being logged but patterns are rarely extracted."
                    ),
                }
            )

        try:
            noise_count = db.execute("""
                SELECT COUNT(*) FROM (
                    SELECT title, COUNT(*) as cnt
                    FROM knowledge_entries
                    WHERE confidence < 0.5
                    GROUP BY title
                    HAVING cnt >= 3
                )
            """).fetchone()[0]
            if noise_count >= 5:
                alerts.append(
                    {
                        "id": "noisy-repeated-titles",
                        "title": f"{noise_count} repeated low-confidence titles detected",
                        "severity": "warning",
                        "detail": (
                            f"{noise_count} distinct low-confidence titles appear 3+ times. "
                            "This indicates noisy extraction; consider tuning extract-knowledge.py thresholds."
                        ),
                    }
                )
            elif noise_count >= 2:
                alerts.append(
                    {
                        "id": "noisy-repeated-titles",
                        "title": f"{noise_count} repeated low-confidence titles detected",
                        "severity": "info",
                        "detail": (
                            f"{noise_count} distinct low-confidence titles appear 3+ times. "
                            "Some noise in extraction pipeline."
                        ),
                    }
                )
        except sqlite3.OperationalError:
            pass

    # ---- Recommended actions ----
    actions = []
    _action_seq = [0]

    def _next_id():
        _action_seq[0] += 1
        return f"action-{_action_seq[0]:02d}"

    if total == 0:
        actions.append(
            {
                "id": _next_id(),
                "title": "Populate the knowledge base",
                "detail": "Index sessions and extract knowledge to start building your knowledge base.",
                "command": "python3 build-session-index.py && python3 extract-knowledge.py",
            }
        )
    else:
        if embed_pct < 30 and total >= 5:
            actions.append(
                {
                    "id": _next_id(),
                    "title": "Build semantic embeddings",
                    "detail": f"Only {embed_pct:.0f}% of entries have embeddings. Semantic search needs more coverage.",
                    "command": "python3 embed.py --build",
                }
            )

        if relation_density < 0.2 and total >= 10:
            actions.append(
                {
                    "id": _next_id(),
                    "title": "Add knowledge relations",
                    "detail": (
                        f"Relation density is low ({relation_density:.2f}). "
                        "Connect related entries to improve cross-session recall."
                    ),
                    "command": "python3 learn.py --help",
                }
            )

        if health.get("categorized_pct", 100) < 80:
            actions.append(
                {
                    "id": _next_id(),
                    "title": "Re-run knowledge extraction",
                    "detail": "Many entries are uncategorized. Re-extracting can improve signal.",
                    "command": "python3 extract-knowledge.py --force",
                }
            )

        if stale_pct >= 40:
            actions.append(
                {
                    "id": _next_id(),
                    "title": "Review stale entries",
                    "detail": f"{stale_pct:.0f}% of entries are stale. Review and prune outdated knowledge.",
                    "command": "python3 query-session.py --mistakes --limit 20",
                }
            )

        if isinstance(mp_ratio, (int, float)) and mp_ratio < 0.5 and health.get("mistakes", 0) >= 3:
            actions.append(
                {
                    "id": _next_id(),
                    "title": "Extract patterns from mistakes",
                    "detail": f"Low pattern/mistake ratio ({mp_ratio:.2f}x). Review mistakes and document learnings.",
                    "command": "python3 query-session.py --mistakes --limit 10",
                }
            )

        if high_conf_pct < 20 and total >= 10:
            actions.append(
                {
                    "id": _next_id(),
                    "title": "Increase entry confidence through reinforcement",
                    "detail": (
                        f"Only {high_conf_pct:.0f}% of entries have high confidence. "
                        "Use learn.py to add manual high-quality entries."
                    ),
                    "command": "python3 learn.py --help",
                }
            )

    # ---- Recurring noise titles ----
    recurring_noise = []
    try:
        noise_rows = db.execute("""
            SELECT title,
                   CASE
                       WHEN COUNT(DISTINCT category) = 1 THEN MIN(category)
                       ELSE 'mixed'
                   END as category,
                   COUNT(*) as entry_count,
                   AVG(confidence) as avg_confidence
            FROM knowledge_entries
            WHERE confidence < 0.5
            GROUP BY title
            HAVING entry_count >= 2
            ORDER BY entry_count DESC, avg_confidence ASC, title ASC
            LIMIT 15
        """).fetchall()
        for row in noise_rows:
            recurring_noise.append(
                {
                    "title": str(row["title"] or ""),
                    "category": str(row["category"] or ""),
                    "entry_count": int(row["entry_count"]),
                    "avg_confidence": round(float(row["avg_confidence"] or 0), 3),
                }
            )
    except sqlite3.OperationalError:
        pass

    # ---- Hot files ----
    hot_files = []
    try:
        file_refs: dict = {}
        rows = db.execute("""
            SELECT affected_files FROM knowledge_entries
            WHERE affected_files IS NOT NULL AND affected_files != ''
        """).fetchall()
        for row in rows:
            raw = row[0]
            if not raw:
                continue
            try:
                items = json.loads(raw)
                if not isinstance(items, list):
                    items = [str(items)]
            except (json.JSONDecodeError, TypeError):
                items = [raw]
            for item in items:
                if isinstance(item, str) and _is_file_path(item):
                    path = item.strip()
                    file_refs[path] = file_refs.get(path, 0) + 1
        hot_files = [
            {"path": p, "references": c} for p, c in sorted(file_refs.items(), key=lambda x: (-x[1], x[0])) if c >= 2
        ][:20]
    except sqlite3.OperationalError:
        pass

    # ---- Entries per category ----
    entries = {}
    for cat in ("mistake", "pattern", "decision", "tool"):
        try:
            cat_rows = db.execute(
                """
                SELECT id, title, confidence, occurrence_count,
                       last_seen, content, session_id
                FROM knowledge_entries
                WHERE category = ?
                ORDER BY confidence DESC, occurrence_count DESC
                LIMIT 10
            """,
                (cat,),
            ).fetchall()
            cat_entries = []
            for r in cat_rows:
                content = str(r["content"] or "")
                summary = content[:200].replace("\n", " ").strip()
                cat_entries.append(
                    {
                        "id": int(r["id"]),
                        "title": str(r["title"] or ""),
                        "confidence": round(float(r["confidence"] or 0), 3),
                        "occurrence_count": int(r["occurrence_count"] or 0),
                        "last_seen": r["last_seen"],
                        "summary": summary,
                        "session_id": r["session_id"],
                    }
                )
            entries[f"{cat}s"] = cat_entries
        except sqlite3.OperationalError:
            entries[f"{cat}s"] = []

    db.close()

    # ---- Summary ----
    score = health.get("score", 0)
    if score >= 80:
        summary = f"Knowledge base is in excellent health ({score}/100) with {total} entries."
    elif score >= 60:
        summary = f"Knowledge base is in good health ({score}/100) with {total} entries. Some areas need attention."
    elif score >= 40:
        summary = f"Knowledge base has fair health ({score}/100) with {total} entries. Several quality issues detected."
    else:
        summary = f"Knowledge base needs work ({score}/100) with {total} entries. Multiple quality issues detected."

    if alerts:
        top_sev = (
            "critical"
            if any(a["severity"] == "critical" for a in alerts)
            else "warning"
            if any(a["severity"] == "warning" for a in alerts)
            else "info"
        )
        summary += f" {len(alerts)} alert(s) including {top_sev}-level issues."

    # ---- Advisory sync-contract signal (read-only; does NOT alter score) ----
    sync_advisory = _compute_sync_advisory(
        total=total,
        mp_ratio=mp_ratio,
        hot_files=hot_files,
        health=health,
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "overview": overview,
        "quality_alerts": alerts,
        "recommended_actions": actions,
        "recurring_noise_titles": recurring_noise,
        "hot_files": hot_files,
        "entries": entries,
        "sync_advisory": sync_advisory,
    }


def format_insights_report(insights: dict) -> str:
    """Format insights as a human-readable dashboard."""
    ov = insights.get("overview", {})
    score = ov.get("health_score", 0)
    total = ov.get("total_entries", 0)

    if score >= 80:
        grade, emoji = "Excellent", "🏆"
    elif score >= 60:
        grade, emoji = "Good", "✅"
    elif score >= 40:
        grade, emoji = "Fair", "🟡"
    else:
        grade, emoji = "Needs Work", "🔴"

    filled = int(score / 5)
    bar = "█" * filled + "░" * (20 - filled)

    lines = [
        "╔══════════════════════════════════════════╗",
        f"║  {emoji} Knowledge Insights: {score}/100 ({grade})",
        f"║  [{bar}]",
        "╚══════════════════════════════════════════╝",
        "",
        insights.get("summary", ""),
        "",
        "📊 Overview",
        f"  Total entries:    {total:,}",
        f"  Sessions:         {ov.get('sessions', 0):,}",
        f"  High confidence:  {ov.get('high_confidence_pct', 0):.1f}%",
        f"  Low confidence:   {ov.get('low_confidence_pct', 0):.1f}%",
        f"  Stale entries:    {ov.get('stale_pct', 0):.1f}%",
        f"  Relation density: {ov.get('relation_density', 0):.2f} rel/entry",
        f"  Embedding cov.:   {ov.get('embedding_pct', 0):.1f}%",
        "",
    ]

    alerts = insights.get("quality_alerts", [])
    if alerts:
        lines.append("🚨 Quality Alerts")
        for alert in alerts:
            sev_icon = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(alert["severity"], "•")
            lines.append(f"  {sev_icon} [{alert['severity'].upper()}] {alert['title']}")
            lines.append(f"       {alert['detail']}")
        lines.append("")

    actions = insights.get("recommended_actions", [])
    if actions:
        lines.append("💡 Recommended Actions")
        for i, action in enumerate(actions, 1):
            lines.append(f"  {i}. {action['title']}")
            lines.append(f"     {action['detail']}")
            lines.append(f"     $ {action['command']}")
        lines.append("")

    noise = insights.get("recurring_noise_titles", [])
    if noise:
        lines.append("🔄 Recurring Low-Quality Titles (noise candidates)")
        lines.append(f"  {'Title':<40} {'Cat':<10} {'Count':>5}  {'AvgConf':>7}")
        lines.append(f"  {'-' * 40} {'-' * 10} {'-' * 5}  {'-' * 7}")
        for n in noise[:10]:
            title = (n["title"] or "")[:39]
            lines.append(f"  {title:<40} {n['category']:<10} {n['entry_count']:>5}  {n['avg_confidence']:>7.3f}")
        lines.append("")

    hot = insights.get("hot_files", [])
    if hot:
        lines.append("🔥 Hot Files (most referenced)")
        for hf in hot[:10]:
            lines.append(f"  {hf['references']:>4}x  {hf['path']}")
        lines.append("")

    entries = insights.get("entries", {})
    for cat_key in ("mistakes", "patterns", "decisions", "tools"):
        cat_entries = entries.get(cat_key, [])
        if cat_entries:
            lines.append(f"📌 Top {cat_key.title()} (by confidence)")
            for e in cat_entries[:5]:
                conf = f"{e['confidence']:.2f}"
                title = (e["title"] or "")[:60]
                lines.append(f"  [{conf}] {title}")
            lines.append("")

    sync_adv = insights.get("sync_advisory", {})
    if sync_adv and sync_adv.get("status") in ("suggest", "review"):
        status = sync_adv["status"].upper()
        icon = "🔵" if sync_adv["status"] == "suggest" else "🟡"
        lines.append(f"{icon} Sync Advisory [{status}]")
        for reason in sync_adv.get("reasons", []):
            lines.append(f"  • {reason}")
        checklist = sync_adv.get("checklist", "docs/SYNC-MATRIX.md")
        lines.append(f"  Reference: {checklist}")
        lines.append("")

    return "\n".join(lines)


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

    lines.extend(
        [
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
        ]
    )

    for cat, cnt in sorted(health["categories"].items(), key=lambda x: -x[1]):
        pct = (cnt / health["total"]) * 100
        bar_len = int(pct / 5)
        lines.append(f"  {cat:12s} {cnt:5,} {'▓' * bar_len}{'░' * (20 - bar_len)} {pct:.0f}%")

    # Subscores
    lines.extend(["", "📐 Subscores (weighted)"])
    for name, val in health["subscores"].items():
        max_val = {
            "categorization": 20,
            "learning_curve": 20,
            "freshness": 15,
            "relation_density": 15,
            "embedding_coverage": 15,
            "confidence_quality": 15,
        }
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
        lines.extend(
            [
                "",
                "Most recent failure:",
                f"  {lf.get('failed_at', '')} {lf.get('error_code', '')} {lf.get('error_message', '')}".strip(),
            ]
        )
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

    if "--insights" in args:
        stale_days = 30
        if "--stale" in args:
            idx = args.index("--stale")
            stale_days = int(args[idx + 1]) if idx + 1 < len(args) else 30
        insights = compute_insights(stale_days=stale_days)
        if "--json" in args:
            print(json.dumps(insights, indent=2, ensure_ascii=False))
        else:
            print(format_insights_report(insights))
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
