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
    python knowledge-health.py --freshness    # List specific stale entries (>90d default)
    python knowledge-health.py --freshness --days 30 --limit 20  # Tune threshold and count
    python knowledge-health.py --freshness --json  # JSON list of stale entries
    python knowledge-health.py --recall       # Recall telemetry dashboard
    python knowledge-health.py --recall --json  # Recall telemetry as JSON
    python knowledge-health.py --sync         # Sync runtime dashboard
    python knowledge-health.py --sync --json  # Sync runtime as JSON
    python knowledge-health.py --insights     # Derived actionable insights dashboard
    python knowledge-health.py --insights --json  # Insights as JSON
    python knowledge-health.py --dedup        # Find near-duplicate entries (dry-run)
    python knowledge-health.py --dedup --dry-run  # Explicitly dry-run (default)
    python knowledge-health.py --dedup --apply    # Mark lower-confidence dupes as superseded
    python knowledge-health.py --dedup --threshold 0.8  # Custom similarity threshold
    python knowledge-health.py --dedup --category mistake  # Restrict to one category
    python knowledge-health.py --dedup --json  # JSON output of duplicate pairs
    python knowledge-health.py --diff                     # Knowledge snapshot diff (last 7 days)
    python knowledge-health.py --diff --since 2024-01-01  # Diff since a specific date
    python knowledge-health.py --diff --days 30           # Diff over the last 30 days
    python knowledge-health.py --diff --json              # JSON output of diff stats
    python knowledge-health.py --export                   # Export knowledge entries (JSON default)
    python knowledge-health.py --export --format markdown # Export as Markdown
    python knowledge-health.py --export --format csv      # Export as CSV
    python knowledge-health.py --export --category mistake --limit 50  # Filter by category
    python knowledge-health.py --export --tag python      # Filter by tag
    python knowledge-health.py --export --since 2024-01-01  # Filter by creation date
    python knowledge-health.py --export --output entries.json  # Write to file
    python knowledge-health.py --archive --older-than 180d  # Archive entries older than 180 days (dry-run)
    python knowledge-health.py --archive --older-than 180d --confirm  # Apply archive
    python knowledge-health.py --archive --older-than 90d --category mistake  # Filter by category
"""

import csv
import io
import json
import os
import re
import sqlite3
import subprocess
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
DB_PATH = Path(os.environ.get("SK_DB_PATH", str(SESSION_STATE / "knowledge.db"))).expanduser()


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


def _wal_connect(path: "str | Path", **kwargs) -> sqlite3.Connection:
    """Open a SQLite connection with WAL journal mode and busy timeout."""
    db = sqlite3.connect(str(path), **kwargs)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    return db


def get_db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        print("Error: Knowledge database not found.", file=sys.stderr)
        sys.exit(1)
    db = _wal_connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    return db


def compute_health(stale_days: int = 30) -> dict:
    """Compute comprehensive health metrics for the knowledge base."""
    db = get_db()

    # Detect soft-delete column (#387): filter it out of all counts
    _ke_cols = {row["name"] for row in db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
    _nd = "AND (deleted_at IS NULL)" if "deleted_at" in _ke_cols else ""

    # Total entries (excluding soft-deleted)
    total = db.execute(f"SELECT COUNT(*) FROM knowledge_entries WHERE 1=1 {_nd}").fetchone()[0]
    if total == 0:
        db.close()
        return {"score": 0, "total": 0, "message": "Empty knowledge base"}

    # Category distribution
    cats = db.execute(f"""
        SELECT category, COUNT(*) as cnt
        FROM knowledge_entries WHERE 1=1 {_nd} GROUP BY category
    """).fetchall()
    cat_counts = {r["category"]: r["cnt"] for r in cats}

    # Categorization rate (entries with non-empty category)
    uncategorized = db.execute(f"""
        SELECT COUNT(*) FROM knowledge_entries
        WHERE (category IS NULL OR category = '') {_nd}
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
        f"""
        SELECT COUNT(*) FROM knowledge_entries
        WHERE last_seen < ? AND last_seen IS NOT NULL AND last_seen != '' {_nd}
    """,
        (cutoff,),
    ).fetchone()[0]
    stale_pct = (stale / total) * 100 if total > 0 else 0

    # Freshness: entries from last 7 days
    week_ago = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 7 * 86400))
    fresh = db.execute(
        f"""
        SELECT COUNT(*) FROM knowledge_entries
        WHERE first_seen >= ? AND first_seen IS NOT NULL {_nd}
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
    high_conf = db.execute(f"""
        SELECT COUNT(*) FROM knowledge_entries WHERE confidence >= 0.8 {_nd}
    """).fetchone()[0]
    low_conf = db.execute(f"""
        SELECT COUNT(*) FROM knowledge_entries WHERE confidence < 0.5 {_nd}
    """).fetchone()[0]

    # Wing/room coverage
    wings = 0
    rooms = 0
    try:
        wings = db.execute(f"""
            SELECT COUNT(DISTINCT wing) FROM knowledge_entries
            WHERE wing IS NOT NULL AND wing != '' {_nd}
        """).fetchone()[0]
        rooms = db.execute(f"""
            SELECT COUNT(DISTINCT room) FROM knowledge_entries
            WHERE room IS NOT NULL AND room != '' {_nd}
        """).fetchone()[0]
    except sqlite3.OperationalError:
        pass

    # Sessions contributing knowledge
    sessions = db.execute(f"""
        SELECT COUNT(DISTINCT session_id) FROM knowledge_entries
        WHERE session_id IS NOT NULL AND session_id != '' {_nd}
    """).fetchone()[0]

    # Concept tag coverage (informational stat only — does NOT affect weighted score)
    concept_tagged = 0
    try:
        has_ect = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='entry_concept_tags'").fetchone()
        if has_ect:
            concept_tagged = db.execute(
                "SELECT COUNT(DISTINCT entry_id) FROM entry_concept_tags WHERE source = 'auto'"
            ).fetchone()[0]
    except sqlite3.OperationalError:
        pass
    concept_tag_coverage_pct = round((concept_tagged / total) * 100, 1) if total > 0 else 0.0

    db.close()

    # Compute composite score (0-100)
    # NOTE: concept_tag_coverage_pct is informational only; it is NOT part of the weighted score.
    scores = {
        "categorization": min(categorized_pct, 100) * 0.20,  # 20%
        "learning_curve": min(mp_ratio * 50, 100) * 0.20,  # 20% — higher ratio = better
        "freshness": min((fresh / max(total, 1)) * 500, 100) * 0.15,  # 15%
        "relation_density": min(relation_density * 100, 100) * 0.15,  # 15%
        "embedding_coverage": min(embed_pct, 100) * 0.15,  # 15%
        "confidence_quality": (high_conf / max(total, 1)) * 100 * 0.15,  # 15%
    }
    total_score = sum(scores.values())

    # Compute toward-100 gap analysis — additive, derived from fixed-weight subscores.
    # Does NOT change total_score. Sorted largest gap first.
    _max_weights = {
        "categorization": 20.0,
        "learning_curve": 20.0,
        "freshness": 15.0,
        "relation_density": 15.0,
        "embedding_coverage": 15.0,
        "confidence_quality": 15.0,
    }
    _total_gap = round(100.0 - total_score, 1)
    _gap_dims: list[dict] = []
    for _dim, _max_w in _max_weights.items():
        _cur = scores[_dim]
        _gap = round(_max_w - _cur, 1)
        _gap_pct = round((_gap / _max_w) * 100.0, 1) if _max_w > 0 else 0.0
        _pct_of_gap = round((_gap / _total_gap) * 100.0, 1) if _total_gap > 0 else 0.0
        _gap_dims.append(
            {
                "dimension": _dim,
                "current": round(_cur, 1),
                "max": _max_w,
                "gap": _gap,
                "gap_pct": _gap_pct,
                "pct_of_total_gap": _pct_of_gap,
            }
        )
    _gap_dims.sort(key=lambda d: -d["gap"])
    toward_100 = {
        "total_gap": _total_gap,
        "dimensions": _gap_dims,
        "top_gaps": _gap_dims[:3],
    }

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
        "concept_tag_coverage_pct": concept_tag_coverage_pct,
        "subscores": {k: round(v, 1) for k, v in scores.items()},
        "toward_100": toward_100,
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
        recurrence_rate = None
        try:
            ke_cols = {r[1] for r in db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
            if "recurrence_after_briefing" in ke_cols:
                total_m = db.execute(
                    "SELECT COUNT(*) FROM knowledge_entries WHERE category='mistake' AND (deleted_at IS NULL OR deleted_at='')"
                ).fetchone()[0]
                recurring_m = db.execute(
                    "SELECT COUNT(*) FROM knowledge_entries WHERE category='mistake' AND recurrence_after_briefing > 0 AND (deleted_at IS NULL OR deleted_at='')"
                ).fetchone()[0]
                recurrence_rate = round(recurring_m / total_m, 4) if total_m > 0 else 0.0
        except Exception:
            pass  # fail-open: recurrence_rate stays None
        return {
            "available": True,
            "total_events": total_events,
            "events_by_surface": events_by_surface,
            "avg_output_by_surface_mode": avg_output,
            "top_no_hit_queries": no_hit_queries,
            "top_repeated_detail_opens": repeated_detail,
            "recurrence_rate": recurrence_rate,
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


def _compute_integrity_lints(db: sqlite3.Connection) -> dict:
    """Compute orphan/dangling/contradiction/stable_id integrity lints.

    Returns a dict with:
      - dangling_relations: int — knowledge_relations referencing missing entries
      - stable_id_collisions: int — stable_id values shared by multiple rows
      - contradictions: list[dict] — entries with Always/Never conflict on same tags
    All counts are 0 and lists are empty when their tables are absent (fail-open).
    """
    result: dict = {
        "dangling_relations": 0,
        "stable_id_collisions": 0,
        "contradictions": [],
    }

    # Dangling relations: source_id or target_id points to a missing knowledge entry
    try:
        result["dangling_relations"] = db.execute("""
            SELECT COUNT(*) FROM knowledge_relations kr
            WHERE NOT EXISTS (SELECT 1 FROM knowledge_entries ke WHERE ke.id = kr.source_id)
               OR NOT EXISTS (SELECT 1 FROM knowledge_entries ke WHERE ke.id = kr.target_id)
        """).fetchone()[0]
    except sqlite3.OperationalError:
        pass

    # Stable_id collisions: same stable_id on more than one row
    try:
        result["stable_id_collisions"] = db.execute("""
            SELECT COUNT(*) FROM (
                SELECT stable_id FROM knowledge_entries
                WHERE stable_id IS NOT NULL AND stable_id != ''
                GROUP BY stable_id
                HAVING COUNT(*) > 1
            )
        """).fetchone()[0]
    except sqlite3.OperationalError:
        pass

    # Contradiction detection: entries sharing the same non-empty tags where
    # one title starts with "Always" (case-insensitive) and another with "Never"
    try:
        rows = db.execute("""
            SELECT a.id, a.title, n.id, n.title, a.tags
            FROM knowledge_entries a
            JOIN knowledge_entries n
              ON LOWER(a.tags) = LOWER(n.tags)
             AND a.id < n.id
            WHERE (
                       (a.title LIKE 'Always%' AND n.title LIKE 'Never%')
                    OR (a.title LIKE 'Never%' AND n.title LIKE 'Always%')
                  )
              AND a.tags IS NOT NULL AND a.tags != ''
            LIMIT 20
        """).fetchall()
        for row in rows:
            result["contradictions"].append(
                {
                    "entry_a_id": int(row[0]),
                    "entry_a_title": str(row[1] or ""),
                    "entry_b_id": int(row[2]),
                    "entry_b_title": str(row[3] or ""),
                    "shared_tags": str(row[4] or ""),
                }
            )
    except sqlite3.OperationalError:
        pass

    return result


def _compute_db_size_lint() -> dict:
    """Check the database file size against a configurable byte budget.

    Reads SK_DB_SIZE_BUDGET_MB from the environment (default 500 MB).
    Returns a dict with size_bytes, budget_bytes, over_budget, size_mb, budget_mb.
    Fails open (size_bytes=0) when the file is missing or unreadable.
    """
    budget_mb = int(os.environ.get("SK_DB_SIZE_BUDGET_MB", "500"))
    budget_bytes = budget_mb * 1024 * 1024
    try:
        size_bytes = int(DB_PATH.stat().st_size) if DB_PATH.exists() else 0
    except OSError:
        size_bytes = 0
    return {
        "size_bytes": size_bytes,
        "budget_bytes": budget_bytes,
        "over_budget": size_bytes > budget_bytes,
        "size_mb": round(size_bytes / (1024 * 1024), 1),
        "budget_mb": round(budget_mb, 1),
    }


def compute_insights(stale_days: int = 30) -> dict:
    """Derive actionable insights from the knowledge base."""
    health = compute_health(stale_days=stale_days)
    db = get_db()

    # Detect soft-delete column (#387) for filtering
    _ke_cols = {row["name"] for row in db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
    _nd = "AND (deleted_at IS NULL)" if "deleted_at" in _ke_cols else ""

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
            noise_count = db.execute(f"""
                SELECT COUNT(*) FROM (
                    SELECT title, COUNT(*) as cnt
                    FROM knowledge_entries
                    WHERE confidence < 0.5 {_nd}
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

        # ---- Toward-100 gap alerts (evidence-based, score-focused) ----
        # These surface the *score impact* of the largest measured gaps.
        # They complement, not replace, the quality alerts above.
        subscores = health.get("subscores", {})
        mistakes_count = health.get("mistakes", 0)
        patterns_count = health.get("patterns", 0)

        cq_score = subscores.get("confidence_quality", 0.0)
        if cq_score < 7.5 and total >= 10:
            cq_gap = round(15.0 - cq_score, 1)
            alerts.append(
                {
                    "id": "confidence-quality-gap",
                    "title": f"Confidence quality scores {cq_score:.1f}/15 — {cq_gap:.1f} points recoverable",
                    "severity": "warning",
                    "detail": (
                        f"Only {high_conf_pct:.0f}% of entries have high confidence (≥0.8). "
                        f"Use learn.py to add or reinforce high-quality entries and recover up to {cq_gap:.1f} health points."
                    ),
                }
            )

        lc_score = subscores.get("learning_curve", 0.0)
        if lc_score < 10.0 and mistakes_count >= 3:
            lc_gap = round(20.0 - lc_score, 1)
            mp_display = mp_ratio if isinstance(mp_ratio, str) else f"{mp_ratio:.2f}"
            alerts.append(
                {
                    "id": "learning-curve-gap",
                    "title": f"Learning curve scores {lc_score:.1f}/20 — {lc_gap:.1f} points recoverable",
                    "severity": "info",
                    "detail": (
                        f"{patterns_count} patterns vs {mistakes_count} mistakes "
                        f"(ratio {mp_display}x). Extracting more patterns from mistakes can recover "
                        f"up to {lc_gap:.1f} health points."
                    ),
                }
            )

        rd_score = subscores.get("relation_density", 0.0)
        if rd_score < 7.5 and total >= 10:
            rd_gap = round(15.0 - rd_score, 1)
            relations_total = health.get("relations", 0) + health.get("entity_relations", 0)
            alerts.append(
                {
                    "id": "relation-density-gap",
                    "title": f"Relation density scores {rd_score:.1f}/15 — {rd_gap:.1f} points recoverable",
                    "severity": "info",
                    "detail": (
                        f"{relations_total} relations across {total} entries "
                        f"({relation_density:.2f} rel/entry). Linking related entries with learn.py "
                        f"can recover up to {rd_gap:.1f} health points."
                    ),
                }
            )

        # ---- Token budget audit (#397) ----
        _TOKEN_BUDGET = 100_000
        try:
            if "est_tokens" in _ke_cols:
                total_tokens = db.execute(
                    f"SELECT COALESCE(SUM(est_tokens), 0) FROM knowledge_entries WHERE est_tokens IS NOT NULL {_nd}"
                ).fetchone()[0]
                total_tokens = int(total_tokens or 0)
                if total_tokens > _TOKEN_BUDGET:
                    over_pct = round((total_tokens / _TOKEN_BUDGET - 1) * 100, 1)
                    alerts.append(
                        {
                            "id": "token-budget-exceeded",
                            "title": f"Context pack exceeds token budget ({total_tokens:,} > {_TOKEN_BUDGET:,})",
                            "severity": "warning",
                            "detail": (
                                f"Active entries use {total_tokens:,} est_tokens, "
                                f"{over_pct:.1f}% over the {_TOKEN_BUDGET:,}-token budget. "
                                "Run knowledge-health.py --evict-candidates to find low-value entries to remove."
                            ),
                        }
                    )
        except sqlite3.OperationalError:
            pass

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
        noise_rows = db.execute(f"""
            SELECT title,
                   CASE
                       WHEN COUNT(DISTINCT category) = 1 THEN MIN(category)
                       ELSE 'mixed'
                   END as category,
                   COUNT(*) as entry_count,
                   AVG(confidence) as avg_confidence
            FROM knowledge_entries
            WHERE confidence < 0.5 {_nd}
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
        rows = db.execute(f"""
            SELECT affected_files FROM knowledge_entries
            WHERE affected_files IS NOT NULL AND affected_files != '' {_nd}
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
                f"""
                SELECT id, title, confidence, occurrence_count,
                       last_seen, content, session_id
                FROM knowledge_entries
                WHERE category = ? {_nd}
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

    # ---- Integrity lints (run BEFORE db.close()) ----
    integrity_lints = _compute_integrity_lints(db)

    db.close()

    # ---- DB size budget ----
    db_size_budget = _compute_db_size_lint()

    # ---- Integrity-lint alerts ----
    if integrity_lints.get("dangling_relations", 0) > 0:
        alerts.append(
            {
                "id": "dangling-relations",
                "title": "Dangling relations detected",
                "severity": "warning",
                "detail": f"{integrity_lints['dangling_relations']} knowledge_relations reference missing entries.",
            }
        )
    if integrity_lints.get("stable_id_collisions", 0) > 0:
        alerts.append(
            {
                "id": "stable-id-collision",
                "title": "Stable-ID collisions detected",
                "severity": "warning",
                "detail": f"{integrity_lints['stable_id_collisions']} stable_id value(s) shared by multiple entries.",
            }
        )
    if integrity_lints.get("contradictions"):
        alerts.append(
            {
                "id": "contradiction-pairs",
                "title": "Contradiction pairs detected",
                "severity": "warning",
                "detail": f"{len(integrity_lints['contradictions'])} potential contradiction pair(s) detected.",
            }
        )
    if db_size_budget.get("over_budget"):
        alerts.append(
            {
                "id": "db-size-over-budget",
                "title": "Database over size budget",
                "severity": "warning",
                "detail": (
                    f"Database size {db_size_budget['size_mb']} MB exceeds budget "
                    f"{db_size_budget['budget_mb']} MB. Consider archiving old sessions."
                ),
            }
        )

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
        "toward_100": health.get("toward_100", {}),
        "integrity_lints": integrity_lints,
        "db_size_budget": db_size_budget,
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

    toward_100 = insights.get("toward_100", {})
    top_gaps = toward_100.get("top_gaps", [])
    total_gap = toward_100.get("total_gap", 0)
    if top_gaps and total_gap > 0:
        lines.append(f"🎯 Toward 100 (total gap: {total_gap:.1f} points)")
        for g in top_gaps:
            dim_label = g["dimension"].replace("_", " ").title()
            pct_label = f"{g['pct_of_total_gap']:.0f}% of gap"
            lines.append(f"  {dim_label:25s}  {g['current']:5.1f}/{g['max']:.0f}  ▲{g['gap']:4.1f}  ({pct_label})")
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

    # ---- Integrity lints ----
    integrity_lints = insights.get("integrity_lints", {})
    db_size_budget = insights.get("db_size_budget", {})
    show_lints = (
        integrity_lints.get("dangling_relations", 0) > 0
        or integrity_lints.get("stable_id_collisions", 0) > 0
        or integrity_lints.get("contradictions")
        or db_size_budget.get("over_budget")
    )
    if show_lints:
        lines.append("🔍 Integrity Lints")
        if integrity_lints.get("dangling_relations", 0) > 0:
            lines.append(
                f"  🟡 Dangling relations: {integrity_lints['dangling_relations']} knowledge_relations reference missing entries"
            )
        if integrity_lints.get("stable_id_collisions", 0) > 0:
            lines.append(
                f"  🟡 Stable-ID collisions: {integrity_lints['stable_id_collisions']} stable_id value(s) shared by >1 entry"
            )
        if integrity_lints.get("contradictions"):
            lines.append(
                f"  🟡 Contradictions: {len(integrity_lints['contradictions'])} Always/Never pair(s) with shared tags"
            )
        if db_size_budget.get("over_budget"):
            lines.append(
                f"  🟡 DB over budget: {db_size_budget.get('size_mb', 0)} MB "
                f"(budget {db_size_budget.get('budget_mb', 500)} MB) — consider archiving old sessions"
            )
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
        "╔══════════════════════════════════════════╗",
        f"║  {emoji} Knowledge Health: {score}/100 ({grade})",
        f"║  [{bar}]",
        "╚══════════════════════════════════════════╝",
        "",
        "📊 Overview",
        f"  Total entries:     {health['total']:,}",
        f"  Sessions:          {health['sessions']:,}",
        f"  Categorized:       {health['categorized_pct']}%",
        f"  Fresh (7d):        {health['fresh_7d']} new entries",
        f"  Stale (>{health['stale_days']}d):      {health['stale_count']} ({health['stale_pct']}%)",
        "",
        "📈 Learning Curve",
        f"  Mistakes:          {health['mistakes']:,}",
        f"  Patterns:          {health['patterns']:,}",
        f"  Pattern/Mistake:   {health['mp_ratio']}x",
    ]

    # Learning curve interpretation
    mp = health["mp_ratio"]
    if isinstance(mp, (int, float)) and mp >= 1.0:
        lines.append("  → ✅ Good: learning from mistakes")
    elif isinstance(mp, (int, float)) and mp > 0:
        lines.append("  → 🟡 Room to improve: more mistakes than patterns")
    else:
        lines.append("  → 🔴 No patterns extracted from mistakes yet")

    lines.extend(
        [
            "",
            "🔗 Knowledge Graph",
            f"  Relations:         {health['relations']:,}",
            f"  Entity relations:  {health['entity_relations']:,}",
            f"  Density:           {health['relation_density']} rel/entry",
            "",
            "🧠 Embeddings",
            f"  Embedded:          {health['embeddings']:,} / {health['total']:,} ({health['embed_pct']}%)",
            "",
            "🏗️ Organization",
            f"  Wings:             {health['wings']}",
            f"  Rooms:             {health['rooms']}",
            f"  High confidence:   {health['high_confidence']:,}",
            f"  Low confidence:    {health['low_confidence']:,}",
            "",
            "📦 Category Breakdown",
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

    toward_100 = health.get("toward_100", {})
    top_gaps = toward_100.get("top_gaps", [])
    t100_total_gap = toward_100.get("total_gap", 0)
    if top_gaps and t100_total_gap > 0:
        lines.extend(["", f"🎯 Toward 100 (total gap: {t100_total_gap:.1f} points)"])
        for g in top_gaps:
            dim_label = g["dimension"].replace("_", " ").title()
            lines.append(
                f"  {dim_label:25s}  {g['current']:5.1f}/{g['max']:.0f}  "
                f"▲{g['gap']:4.1f}  ({g['pct_of_total_gap']:.0f}% of gap)"
            )

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

    recurrence_rate = stats.get("recurrence_rate")
    if recurrence_rate is not None:
        pct = round(recurrence_rate * 100, 1)
        lines.append("")
        lines.append(f"Mistake recurrence rate: {pct}%  (fraction of mistakes re-encountered after a briefing)")

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


def compute_confidence_decay(stale_days: int = 90, decay_rate: float = 0.05) -> dict:
    """Apply confidence decay to entries not recalled recently (#400).

    Reduces confidence by decay_rate for active entries whose last_seen is
    older than stale_days. Operates only when the decay is non-trivial (>0).
    Returns a summary with decayed_count and list of updated entries.
    """
    if decay_rate <= 0:
        return {"decayed_count": 0, "entries": []}

    db = get_db()
    _ke_cols = {row["name"] for row in db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
    _nd = "AND (deleted_at IS NULL)" if "deleted_at" in _ke_cols else ""

    cutoff = time.strftime("%Y-%m-%d", time.gmtime(time.time() - stale_days * 86400))
    try:
        rows = db.execute(
            f"""
            SELECT id, title, confidence
            FROM knowledge_entries
            WHERE last_seen < ? AND last_seen IS NOT NULL AND last_seen != ''
              AND confidence > 0 {_nd}
            ORDER BY confidence ASC
            """,
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        db.close()
        return {"decayed_count": 0, "entries": []}

    updated = []
    for row in rows:
        new_conf = max(0.0, round(float(row["confidence"] or 0) - decay_rate, 4))
        try:
            db.execute(
                "UPDATE knowledge_entries SET confidence = ? WHERE id = ?",
                (new_conf, row["id"]),
            )
            updated.append(
                {
                    "id": int(row["id"]),
                    "title": str(row["title"] or ""),
                    "old_confidence": float(row["confidence"] or 0),
                    "new_confidence": new_conf,
                }
            )
        except sqlite3.OperationalError:
            pass
    db.commit()
    db.close()
    return {"decayed_count": len(updated), "entries": updated}


def compute_decay_preview(limit: int = 20, half_life_days: float = 30.0) -> dict:
    """Read-only decay preview for knowledge entries (#854).

    Computes recency_decay for every active entry using the Ebbinghaus formula
    exp(-ln(2) * age_days / half_life_days).  Entries are ranked ascending by
    recency_decay (most-decayed first).  No DB writes are performed.

    Returns:
        {
          "entries": [{"id", "title", "category", "age_days", "recency_decay",
                        "confidence", "projected_delta"}, ...],  # capped at limit
          "total": int,      # total active entries considered
          "tiers": {"fresh": int, "stale": int, "decaying": int, "dead": int,
                    "unknown": int},
          "half_life_days": float,
        }
    """
    import math

    db = get_db()
    _ke_cols = {row["name"] for row in db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
    _nd = "AND (deleted_at IS NULL)" if "deleted_at" in _ke_cols else ""
    _has_last_accessed = "last_accessed_at" in _ke_cols
    _last_accessed_sel = ", last_accessed_at" if _has_last_accessed else ""

    try:
        rows = db.execute(
            f"""
            SELECT id, title, category, confidence, last_seen{_last_accessed_sel}
            FROM knowledge_entries
            WHERE confidence > 0 {_nd}
            ORDER BY id
            """,
        ).fetchall()
    except sqlite3.OperationalError:
        db.close()
        return {"entries": [], "total": 0, "tiers": {}, "half_life_days": half_life_days}
    db.close()

    now_dt = datetime.now(timezone.utc).replace(tzinfo=None)

    def _age(ts_str: str | None) -> float | None:
        if not ts_str:
            return None
        try:
            s = str(ts_str)[:19].replace("T", " ")
            ts = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
            return max(0.0, (now_dt - ts).total_seconds() / 86400.0)
        except Exception:
            return None

    def _decay(age_days: float | None) -> float:
        if age_days is None or half_life_days <= 0:
            return 1.0
        return math.exp(-math.log(2) * age_days / half_life_days)

    entries = []
    for row in rows:
        ts = (row["last_accessed_at"] if _has_last_accessed and row["last_accessed_at"] else None) or row["last_seen"]
        age_days = _age(ts)
        conf = float(row["confidence"] or 0.0)
        rd = _decay(age_days)
        entries.append(
            {
                "id": int(row["id"]),
                "title": str(row["title"] or ""),
                "category": str(row["category"] or ""),
                "age_days": round(age_days, 1) if age_days is not None else None,
                "recency_decay": round(rd, 4),
                "confidence": conf,
                "projected_delta": round(conf * (rd - 1.0), 4),
            }
        )

    entries.sort(key=lambda e: e["recency_decay"])

    fresh = sum(1 for e in entries if e["age_days"] is not None and e["age_days"] < 7)
    stale = sum(1 for e in entries if e["age_days"] is not None and 7 <= e["age_days"] < 30)
    decaying = sum(1 for e in entries if e["age_days"] is not None and 30 <= e["age_days"] < 90)
    dead = sum(1 for e in entries if e["age_days"] is not None and e["age_days"] >= 90)
    unknown = sum(1 for e in entries if e["age_days"] is None)

    return {
        "entries": entries[:limit],
        "total": len(entries),
        "tiers": {"fresh": fresh, "stale": stale, "decaying": decaying, "dead": dead, "unknown": unknown},
        "half_life_days": half_life_days,
    }


def format_decay_preview(result: dict) -> str:
    """Format compute_decay_preview() output as a human-readable dashboard."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    tiers = result.get("tiers", {})
    entries = result.get("entries", [])
    hl = result.get("half_life_days", 30.0)
    lines = [
        f"Knowledge Decay Dashboard ({today})  [half-life={hl:.0f}d]",
        "━" * 50,
        f"🟢 Fresh     (accessed <  7d):  {tiers.get('fresh', 0):4d} entries",
        f"🟡 Stale     ( 7–30d no access): {tiers.get('stale', 0):4d} entries",
        f"🔴 Decaying  (30–90d no access): {tiers.get('decaying', 0):4d} entries",
        f"💀 Dead      (90d+  no access):  {tiers.get('dead', 0):4d} entries",
        "",
    ]
    if entries:
        lines.append(f"Top {len(entries)} entries needing refresh (most decayed first):")
        for e in entries:
            age_str = f"{e['age_days']}d" if e["age_days"] is not None else "n/a"
            conf = e["confidence"]
            proj = conf + e["projected_delta"]
            delta_str = f"{e['projected_delta']:+.4f}"
            lines.append(
                f"  #{e['id']:<6d} [{e['category']:<10s}] {e['title'][:45]:<45}"
                f"  last={age_str:<5}  decay={e['recency_decay']:.4f}"
                f"  conf={conf:.2f}→{max(0.0, proj):.2f} (Δ{delta_str})"
            )
    else:
        lines.append("  ✅ No active entries found.")
    lines.append("")
    lines.append("Run `sk learn --amend <id> --confidence <val>` to refresh entries.")
    return "\n".join(lines)


def compute_eviction_candidates(limit: int = 20) -> dict:
    """Score active entries and return low-value eviction candidates (#401).

    Score = confidence * (1 / max(age_days, 1)) * occurrence_count
    Lower score = more evictable. Returns the bottom `limit` entries.
    """
    db = get_db()
    _ke_cols = {row["name"] for row in db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
    _nd = "AND (deleted_at IS NULL)" if "deleted_at" in _ke_cols else ""

    now_ts = time.time()
    try:
        rows = db.execute(
            f"""
            SELECT id, title, category, confidence, occurrence_count, first_seen, last_seen
            FROM knowledge_entries
            WHERE 1=1 {_nd}
            ORDER BY id ASC
            """,
        ).fetchall()
    except sqlite3.OperationalError:
        db.close()
        return {"candidates": []}

    scored = []
    for row in rows:
        try:
            first = row["first_seen"] or ""
            if first:
                age_days = max((now_ts - time.mktime(time.strptime(first[:10], "%Y-%m-%d"))) / 86400, 1)
            else:
                age_days = 1
            conf = float(row["confidence"] or 0.0)
            occ = max(int(row["occurrence_count"] or 1), 1)
            score = conf * (1.0 / age_days) * occ
            scored.append(
                {
                    "id": int(row["id"]),
                    "title": str(row["title"] or ""),
                    "category": str(row["category"] or ""),
                    "confidence": round(conf, 4),
                    "occurrence_count": occ,
                    "age_days": round(age_days, 1),
                    "eviction_score": round(score, 6),
                }
            )
        except (ValueError, OverflowError, KeyError):
            pass

    db.close()
    scored.sort(key=lambda x: x["eviction_score"])
    return {"candidates": scored[:limit]}


def _jaccard_similarity(text_a: str, text_b: str) -> float:
    """Return Jaccard similarity between two strings using unigram token sets.

    Tokens are lowercased, non-alphanumeric characters stripped.
    Returns 0.0 if either token set is empty.
    """

    def _tokenize(s: str) -> set:
        return {t.lower() for t in re.findall(r"[a-z0-9]+", s.lower()) if t}

    tokens_a = _tokenize(text_a)
    tokens_b = _tokenize(text_b)
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    return intersection / union if union > 0 else 0.0


def _insert_supersedes_relation(db: sqlite3.Connection, source_id: int, target_id: int) -> None:
    """Insert a SUPERSEDES relation from source_id → target_id in knowledge_relations.

    source_id is the surviving (higher-confidence) entry.
    target_id is the near-duplicate to be marked superseded.
    Idempotent via INSERT OR IGNORE.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    db.execute(
        """
        INSERT OR IGNORE INTO knowledge_relations
            (source_id, target_id, relation_type, confidence, created_at, session_id)
        VALUES (?, ?, 'SUPERSEDES', 1.0, ?, '')
        """,
        (source_id, target_id, now),
    )
    db.commit()


def compute_dedup_candidates(threshold: float = 0.7, category: str | None = None) -> dict:
    """Find near-duplicate knowledge entries using Jaccard similarity on title+content tokens.

    Entries are compared only within the same (category, wing, room) bucket to limit
    combinatorial explosion.  Only pairs with similarity >= threshold are returned.

    Args:
        threshold: Minimum Jaccard similarity to report (default 0.7).
        category: Restrict comparison to a single category (e.g. 'mistake').

    Returns a dict with keys:
        threshold, category, pairs: list of {id_a, id_b, similarity, title_a, title_b,
        conf_a, conf_b, superseded_id (the lower-confidence one)}.
    """
    db = get_db()
    _ke_cols = {row["name"] for row in db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
    _nd = "AND (deleted_at IS NULL)" if "deleted_at" in _ke_cols else ""

    params: list = []
    cat_filter = ""
    if category:
        cat_filter = "AND category = ?"
        params.append(category)

    rows = db.execute(
        f"""
        SELECT id, category, wing, room, title, content, confidence
        FROM knowledge_entries
        WHERE 1=1 {_nd} {cat_filter}
        ORDER BY category, wing, room, id
        """,
        params,
    ).fetchall()
    db.close()

    # Group into (category, wing, room) buckets
    buckets: dict[tuple, list] = {}
    for r in rows:
        key = (r["category"] or "", r["wing"] or "", r["room"] or "")
        buckets.setdefault(key, []).append(r)

    pairs = []
    for entries in buckets.values():
        n = len(entries)
        for i in range(n):
            for j in range(i + 1, n):
                a = entries[i]
                b = entries[j]
                text_a = (a["title"] or "") + " " + (a["content"] or "")
                text_b = (b["title"] or "") + " " + (b["content"] or "")
                sim = _jaccard_similarity(text_a, text_b)
                if sim >= threshold:
                    conf_a = float(a["confidence"] or 0.0)
                    conf_b = float(b["confidence"] or 0.0)
                    # The lower-confidence entry is the candidate for superseding
                    superseded_id = b["id"] if conf_a >= conf_b else a["id"]
                    surviving_id = a["id"] if conf_a >= conf_b else b["id"]
                    pairs.append(
                        {
                            "id_a": int(a["id"]),
                            "id_b": int(b["id"]),
                            "similarity": round(sim, 4),
                            "title_a": str(a["title"] or ""),
                            "title_b": str(b["title"] or ""),
                            "conf_a": round(conf_a, 4),
                            "conf_b": round(conf_b, 4),
                            "superseded_id": int(superseded_id),
                            "surviving_id": int(surviving_id),
                        }
                    )

    pairs.sort(key=lambda p: p["similarity"], reverse=True)
    return {"threshold": threshold, "category": category, "pairs": pairs}


def format_dedup_report(result: dict, dry_run: bool = True) -> str:
    """Format dedup candidates as a human-readable table."""
    pairs = result["pairs"]
    thr = result["threshold"]
    cat = result.get("category") or "all"
    lines = [
        f"🔍 Near-duplicate knowledge entries  (threshold={thr:.2f}, category={cat})",
        f"   Found {len(pairs)} pair(s).",
    ]
    if not pairs:
        lines.append("   ✅ No near-duplicates detected.")
        return "\n".join(lines)

    lines.append("")
    header = f"  {'ID-A':>6}  {'ID-B':>6}  {'Sim':>6}  {'Conf-A':>6}  {'Conf-B':>6}  Title-A / Title-B"
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))
    for p in pairs:
        lines.append(
            f"  #{p['id_a']:5d}  #{p['id_b']:5d}  {p['similarity']:6.3f}"
            f"  {p['conf_a']:6.3f}  {p['conf_b']:6.3f}"
            f"  {p['title_a'][:40]!r}"
        )
        lines.append(f"  {'':>6}  {'':>6}  {'':>6}  {'':>6}  {'':>6}  vs {p['title_b'][:40]!r}")
        lines.append(f"  {'':>6}  {'':>6}  {'':>6}  {'':>6}  {'':>6}  → supersede #{p['superseded_id']}")

    if dry_run:
        lines.append("\n  (dry-run — use --apply to mark superseded entries)")
    return "\n".join(lines)


def compute_diff_stats(since: str | None = None, days: int = 7) -> dict:
    """Compute a knowledge snapshot diff over a time window.

    Args:
        since: ISO date string ``YYYY-MM-DD`` for the cutoff start.  When
               provided, *days* is ignored.
        days:  Number of days back from now to use as the cutoff (default 7).

    Returns a dict with keys:
        cutoff, days, new_count, resolved_count, bumped_recurrence_count,
        category_delta (dict category→count of new entries),
        top_new_tags (list of {tag, count} sorted by count desc),
        new_entries (list of {id, category, title, first_seen}),
        resolved_entries (list of {id, category, title, last_seen}),
        bumped_entries (list of {id, category, title, recurrence_after_briefing}).
    """
    db = get_db()
    _ke_cols = {row["name"] for row in db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
    _nd = "AND (deleted_at IS NULL)" if "deleted_at" in _ke_cols else ""

    if since:
        cutoff = since
    else:
        cutoff = time.strftime("%Y-%m-%dT00:00:00", time.gmtime(time.time() - days * 86400))

    # New entries created on/after cutoff
    new_rows = db.execute(
        f"""
        SELECT id, category, title, first_seen, tags
        FROM knowledge_entries
        WHERE first_seen >= ? {_nd}
        ORDER BY first_seen DESC
        """,
        (cutoff,),
    ).fetchall()

    # Resolved entries updated on/after cutoff (use last_seen as update proxy)
    resolved_rows: list = []
    if "is_resolved" in _ke_cols:
        resolved_rows = db.execute(
            f"""
            SELECT id, category, title, last_seen
            FROM knowledge_entries
            WHERE is_resolved = 1 AND last_seen >= ? {_nd}
            ORDER BY last_seen DESC
            """,
            (cutoff,),
        ).fetchall()

    # Bumped recurrence entries active in window
    bumped_rows: list = []
    if "recurrence_after_briefing" in _ke_cols:
        bumped_rows = db.execute(
            f"""
            SELECT id, category, title, recurrence_after_briefing
            FROM knowledge_entries
            WHERE recurrence_after_briefing > 0 AND last_seen >= ? {_nd}
            ORDER BY recurrence_after_briefing DESC
            """,
            (cutoff,),
        ).fetchall()

    db.close()

    # Category delta: count new entries per category
    category_delta: dict[str, int] = {}
    for r in new_rows:
        cat = r["category"] or "uncategorized"
        category_delta[cat] = category_delta.get(cat, 0) + 1

    # Top new tags: parse comma-separated tags from new entries
    tag_counts: dict[str, int] = {}
    for r in new_rows:
        for tag in (r["tags"] or "").split(","):
            tag = tag.strip()
            if tag:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

    top_new_tags = sorted(
        [{"tag": t, "count": c} for t, c in tag_counts.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:10]

    return {
        "cutoff": cutoff,
        "days": days,
        "new_count": len(new_rows),
        "resolved_count": len(resolved_rows),
        "bumped_recurrence_count": len(bumped_rows),
        "category_delta": category_delta,
        "top_new_tags": top_new_tags,
        "new_entries": [
            {
                "id": int(r["id"]),
                "category": r["category"] or "",
                "title": r["title"] or "",
                "first_seen": r["first_seen"] or "",
            }
            for r in new_rows
        ],
        "resolved_entries": [
            {
                "id": int(r["id"]),
                "category": r["category"] or "",
                "title": r["title"] or "",
                "last_seen": r["last_seen"] or "",
            }
            for r in resolved_rows
        ],
        "bumped_entries": [
            {
                "id": int(r["id"]),
                "category": r["category"] or "",
                "title": r["title"] or "",
                "recurrence_after_briefing": int(r["recurrence_after_briefing"] or 0),
            }
            for r in bumped_rows
        ],
    }


def format_diff_report(result: dict) -> str:
    """Format a knowledge diff result as a human-readable table."""
    cutoff = result.get("cutoff", "?")[:10]
    new_count = result.get("new_count", 0)
    resolved_count = result.get("resolved_count", 0)
    bumped_count = result.get("bumped_recurrence_count", 0)
    cat_delta = result.get("category_delta", {})
    top_tags = result.get("top_new_tags", [])

    lines = [
        f"📊 Knowledge Diff  (since {cutoff})",
        f"   +{new_count} new  |  -{resolved_count} resolved  |  ↑{bumped_count} bumped recurrence",
    ]

    if cat_delta:
        lines.append("")
        lines.append("  Category breakdown (new entries):")
        for cat, cnt in sorted(cat_delta.items(), key=lambda x: -x[1]):
            lines.append(f"    {cat:<14}  +{cnt}")

    if top_tags:
        tag_str = "  ".join(f"{t['tag']}({t['count']})" for t in top_tags[:8])
        lines.append("")
        lines.append(f"  Top new tags:  {tag_str}")

    new_entries = result.get("new_entries", [])
    if new_entries:
        lines.append("")
        lines.append(f"  New entries ({len(new_entries)}):")
        for e in new_entries[:10]:
            lines.append(
                f"    #{e['id']:6d}  [{e['category']:12s}]  {e['title'][:55]:<55}  {(e['first_seen'] or '')[:10]}"
            )
        if len(new_entries) > 10:
            lines.append(f"    … and {len(new_entries) - 10} more")

    resolved_entries = result.get("resolved_entries", [])
    if resolved_entries:
        lines.append("")
        lines.append(f"  Resolved entries ({len(resolved_entries)}):")
        for e in resolved_entries[:10]:
            lines.append(
                f"    #{e['id']:6d}  [{e['category']:12s}]  {e['title'][:55]:<55}  {(e['last_seen'] or '')[:10]}"
            )
        if len(resolved_entries) > 10:
            lines.append(f"    … and {len(resolved_entries) - 10} more")

    bumped_entries = result.get("bumped_entries", [])
    if bumped_entries:
        lines.append("")
        lines.append(f"  Bumped recurrence ({len(bumped_entries)}):")
        for e in bumped_entries[:10]:
            lines.append(
                f"    #{e['id']:6d}  [{e['category']:12s}]  {e['title'][:55]:<55}  recurrence={e['recurrence_after_briefing']}"
            )
        if len(bumped_entries) > 10:
            lines.append(f"    … and {len(bumped_entries) - 10} more")

    if not new_entries and not resolved_entries and not bumped_entries:
        lines.append("   ✅ No changes in this window.")

    return "\n".join(lines)


def compute_knowledge_export(
    fmt: str = "json",
    category: str | None = None,
    tag: str | None = None,
    limit: int = 500,
    since: str | None = None,
) -> dict:
    """Query knowledge entries with optional filters and return structured rows.

    Args:
        fmt:      Output format hint (json|markdown|csv) — stored in result for callers.
        category: Restrict to a single category (e.g. 'mistake').
        tag:      Restrict to entries whose tags column contains this value.
        limit:    Maximum number of rows to return (default 500).
        since:    ISO date string ``YYYY-MM-DD``; only entries with first_seen >= this date.

    Returns a dict with:
        format, category, tag, since, limit, count, entries (list of dicts).
    """
    db = get_db()
    _ke_cols = {row["name"] for row in db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
    _nd = "AND (deleted_at IS NULL)" if "deleted_at" in _ke_cols else ""

    conditions: list[str] = [f"1=1 {_nd}"]
    params: list = []

    if category:
        conditions.append("category = ?")
        params.append(category)

    if tag:
        conditions.append("(',' || tags || ',' LIKE '%,' || ? || ',%' OR tags = ?)")
        params.append(tag)
        params.append(tag)

    if since:
        conditions.append("first_seen >= ?")
        params.append(since)

    where = " AND ".join(conditions)
    params.append(limit)

    try:
        rows = db.execute(
            f"""
            SELECT id, session_id, category, title, content, tags,
                   confidence, occurrence_count, first_seen, last_seen,
                   wing, room, affected_files, facts, est_tokens, task_id,
                   source_file, start_line, end_line
            FROM knowledge_entries
            WHERE {where}
            ORDER BY confidence DESC, first_seen DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    except sqlite3.OperationalError:
        db.close()
        return {
            "format": fmt,
            "category": category,
            "tag": tag,
            "since": since,
            "limit": limit,
            "count": 0,
            "entries": [],
        }

    db.close()

    _JSON_ARRAY_FIELDS = ("affected_files", "facts")
    entries = []
    for r in rows:
        d = dict(r)
        for field in _JSON_ARRAY_FIELDS:
            if field in d and isinstance(d[field], str):
                try:
                    d[field] = json.loads(d[field])
                except (ValueError, TypeError):
                    pass
        entries.append(d)

    return {
        "format": fmt,
        "category": category,
        "tag": tag,
        "since": since,
        "limit": limit,
        "count": len(entries),
        "entries": entries,
    }


def _format_export_json(result: dict) -> str:
    """Serialize export result as JSON string."""
    output = json.dumps(result, indent=2, ensure_ascii=False, default=str)
    return output


def _format_export_markdown(result: dict) -> str:
    """Render export result as Markdown."""
    entries = result.get("entries", [])
    cat_label = (result.get("category") or "all").title()
    lines = [f"# Knowledge Export — {cat_label}\n"]
    if result.get("since"):
        lines.append(f"_Entries since {result['since']}_\n")
    lines.append(f"_{len(entries)} entries_\n")
    for i, e in enumerate(entries, 1):
        title = e.get("title") or "(untitled)"
        category = e.get("category") or ""
        confidence = e.get("confidence") or 0.0
        tags = e.get("tags") or ""
        first_seen = (e.get("first_seen") or "")[:10]
        content = e.get("content") or ""
        lines.append(f"## {i}. {title}\n")
        lines.append(f"- **Category**: {category}")
        lines.append(f"- **Confidence**: {float(confidence):.2f}")
        if tags:
            lines.append(f"- **Tags**: {tags}")
        if first_seen:
            lines.append(f"- **First seen**: {first_seen}")
        lines.append(f"\n{content[:1000]}\n")
        lines.append("---\n")
    return "\n".join(lines)


def _format_export_csv(result: dict) -> str:
    """Render export result as CSV string."""
    entries = result.get("entries", [])
    buf = io.StringIO()
    fieldnames = ["id", "category", "title", "confidence", "tags", "first_seen", "last_seen", "content"]
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for e in entries:
        row = {k: e.get(k, "") for k in fieldnames}
        # Truncate long content for CSV readability
        if isinstance(row.get("content"), str) and len(row["content"]) > 500:
            row["content"] = row["content"][:500] + "…"
        writer.writerow(row)
    return buf.getvalue()


def _parse_older_than(value: str) -> int:
    """Parse an 'Nd' age spec into integer days.  e.g. '180d' → 180, '90' → 90."""
    v = value.strip().lower()
    if v.endswith("d"):
        v = v[:-1]
    try:
        return max(1, int(v))
    except ValueError:
        raise ValueError(f"Cannot parse --older-than {value!r}: expected integer or 'Nd' (e.g. '180d')") from None


def run_knowledge_archive(
    older_than_days: int = 180,
    category: str | None = None,
    dry_run: bool = True,
) -> dict:
    """Soft-delete knowledge entries older than *older_than_days* days.

    Uses the ``first_seen`` column as the creation timestamp.
    Operates in chunks of 500 to avoid locking the DB for long periods.

    Args:
        older_than_days: Archive entries whose first_seen is older than this many days.
        category:        Restrict to a single category; None means all categories.
        dry_run:         When True, only counts matching entries (no writes).

    Returns a dict with:
        older_than_days, category, dry_run, preview_count, archived_count.
    """
    db = get_db()
    _ke_cols = {row["name"] for row in db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}

    if "deleted_at" not in _ke_cols:
        db.close()
        return {
            "older_than_days": older_than_days,
            "category": category,
            "dry_run": dry_run,
            "preview_count": 0,
            "archived_count": 0,
            "error": "deleted_at column not present; soft-delete migration not applied",
        }

    cutoff = time.strftime("%Y-%m-%d", time.gmtime(time.time() - older_than_days * 86400))

    conditions: list[str] = ["first_seen < ?", "first_seen IS NOT NULL", "first_seen != ''", "(deleted_at IS NULL)"]
    params: list = [cutoff]

    if category:
        conditions.append("category = ?")
        params.append(category)

    where = " AND ".join(conditions)

    # Preview count
    count_row = db.execute(
        f"SELECT COUNT(*) FROM knowledge_entries WHERE {where}",
        params,
    ).fetchone()
    preview_count = int(count_row[0] if count_row else 0)

    if dry_run or preview_count == 0:
        db.close()
        return {
            "older_than_days": older_than_days,
            "category": category,
            "dry_run": dry_run,
            "preview_count": preview_count,
            "archived_count": 0,
        }

    # Batch UPDATE in chunks of 500
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    archived_total = 0
    chunk_size = 500

    while True:
        id_rows = db.execute(
            f"SELECT id FROM knowledge_entries WHERE {where} LIMIT ?",
            params + [chunk_size],
        ).fetchall()
        if not id_rows:
            break
        ids = [r[0] for r in id_rows]
        placeholders = ",".join("?" * len(ids))
        db.execute(
            f"UPDATE knowledge_entries SET deleted_at = ? WHERE id IN ({placeholders})",
            [now_ts] + ids,
        )
        db.commit()
        archived_total += len(ids)
        if len(ids) < chunk_size:
            break

    db.close()
    return {
        "older_than_days": older_than_days,
        "category": category,
        "dry_run": False,
        "preview_count": preview_count,
        "archived_count": archived_total,
    }


def cmd_list(args: list) -> None:
    """List knowledge entries with structured filters."""
    wing = None
    room = None
    tag = None
    priority = None
    category = None
    limit = 20
    since_days = None
    as_json = "--json" in args

    if "--wing" in args:
        idx = args.index("--wing")
        wing = args[idx + 1] if idx + 1 < len(args) else None
    if "--room" in args:
        idx = args.index("--room")
        room = args[idx + 1] if idx + 1 < len(args) else None
    if "--tag" in args:
        idx = args.index("--tag")
        tag = args[idx + 1] if idx + 1 < len(args) else None
    if "--priority" in args:
        idx = args.index("--priority")
        priority = args[idx + 1] if idx + 1 < len(args) else None
    if "--category" in args:
        idx = args.index("--category")
        category = args[idx + 1] if idx + 1 < len(args) else None
    if "--limit" in args:
        idx = args.index("--limit")
        try:
            limit = int(args[idx + 1]) if idx + 1 < len(args) else 20
        except (ValueError, IndexError):
            limit = 20
    if "--since" in args:
        idx = args.index("--since")
        try:
            since_days = int(args[idx + 1]) if idx + 1 < len(args) else None
        except (ValueError, IndexError):
            since_days = None

    db = get_db()
    try:
        base = (
            "SELECT ke.id, ke.title, ke.category, ke.priority, ke.wing, ke.room, ke.first_seen"
            " FROM knowledge_entries ke"
        )
        where: list[str] = ["ke.deleted_at IS NULL"]
        params: list = []

        if tag:
            base += " JOIN entry_concept_tags ect ON ke.id = ect.entry_id"
            where.append("ect.tag = ?")
            params.append(tag)
        if wing:
            where.append("ke.wing = ?")
            params.append(wing)
        if room:
            where.append("ke.room = ?")
            params.append(room)
        if priority:
            where.append("ke.priority = ?")
            params.append(priority)
        if category:
            where.append("ke.category = ?")
            params.append(category)
        if since_days is not None:
            where.append("ke.first_seen >= datetime('now', ?)")
            params.append(f"-{since_days} days")

        query = base + " WHERE " + " AND ".join(where)
        query += " ORDER BY ke.first_seen DESC LIMIT ?"
        params.append(limit)

        cursor = db.execute(query, params)
        rows = cursor.fetchall()
    finally:
        db.close()

    if as_json:
        result = [
            {
                "id": row[0],
                "title": row[1],
                "category": row[2],
                "priority": row[3],
                "wing": row[4],
                "room": row[5],
                "first_seen": row[6],
            }
            for row in rows
        ]
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    if not rows:
        print("No entries found.")
        return

    print(f"{'ID':8}  {'PRI':5}  {'CATEGORY':12}  {'TITLE':60}  WING/ROOM")
    print("-" * 100)
    for row in rows:
        id_short = str(row[0] or "")[:8]
        pri = (row[3] or "")[:5]
        cat = (row[2] or "")[:12]
        title = (row[1] or "")[:60]
        wr = f"{row[4] or ''}/{row[5] or ''}".strip("/")
        print(f"{id_short:8}  {pri:5}  {cat:12}  {title:60}  {wr}")


def main():
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print(__doc__)
        return

    if "--list" in args:
        cmd_list(args)
        return

    if "--dedup" in args:
        threshold = 0.7
        category = None
        dry_run = "--dry-run" in args
        apply_flag = "--apply" in args
        if "--threshold" in args:
            idx = args.index("--threshold")
            try:
                threshold = float(args[idx + 1]) if idx + 1 < len(args) else 0.7
            except (ValueError, IndexError):
                threshold = 0.7
        if "--category" in args:
            idx = args.index("--category")
            category = args[idx + 1] if idx + 1 < len(args) else None
        try:
            result = compute_dedup_candidates(threshold=threshold, category=category)
            if "--json" in args:
                print(json.dumps(result, indent=2, ensure_ascii=False))
            else:
                print(format_dedup_report(result, dry_run=not apply_flag))
            if apply_flag and not dry_run and result["pairs"]:
                db = get_db()
                applied = 0
                for p in result["pairs"]:
                    try:
                        _insert_supersedes_relation(db, p["surviving_id"], p["superseded_id"])
                        applied += 1
                    except Exception as exc:
                        print(f"  ⚠ Could not mark #{p['superseded_id']}: {exc}", file=sys.stderr)
                db.close()
                print(f"\n  ✅ Marked {applied} entr(ies) as superseded.")
        except Exception as exc:
            print(f"⚠ dedup failed: {exc}", file=sys.stderr)
        return

    if "--diff" in args:
        since = None
        days = 7
        if "--since" in args:
            idx = args.index("--since")
            since = args[idx + 1] if idx + 1 < len(args) else None
        if "--days" in args:
            idx = args.index("--days")
            try:
                days = int(args[idx + 1]) if idx + 1 < len(args) else 7
            except (ValueError, IndexError):
                days = 7
        try:
            result = compute_diff_stats(since=since, days=days)
            if "--json" in args:
                print(json.dumps(result, indent=2, ensure_ascii=False))
            else:
                print(format_diff_report(result))
        except Exception as exc:
            print(f"⚠ diff failed: {exc}", file=sys.stderr)
        return

    if "--archive" in args:
        older_than_str = "180d"
        if "--older-than" in args:
            idx = args.index("--older-than")
            older_than_str = args[idx + 1] if idx + 1 < len(args) else "180d"
        category = None
        if "--category" in args:
            idx = args.index("--category")
            category = args[idx + 1] if idx + 1 < len(args) else None
        dry_run = "--confirm" not in args

        try:
            days = _parse_older_than(older_than_str)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)

        result = run_knowledge_archive(older_than_days=days, category=category, dry_run=dry_run)

        if "--json" in args:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            cat_label = category or "all"
            if result.get("error"):
                print(f"⚠ archive unavailable: {result['error']}", file=sys.stderr)
            elif dry_run:
                print(
                    f"📦 Archive preview  (--older-than {older_than_str}, category={cat_label}):\n"
                    f"   {result['preview_count']} entr(ies) would be archived.\n"
                    f"   Run with --confirm to apply."
                )
            else:
                print(
                    f"✅ Archived {result['archived_count']} entr(ies)  "
                    f"(older-than={older_than_str}, category={cat_label})"
                )
        return

    if "--export" in args:
        fmt = "json"
        if "--format" in args:
            idx = args.index("--format")
            fmt = args[idx + 1] if idx + 1 < len(args) else "json"
        category = None
        if "--category" in args:
            idx = args.index("--category")
            category = args[idx + 1] if idx + 1 < len(args) else None
        tag = None
        if "--tag" in args:
            idx = args.index("--tag")
            tag = args[idx + 1] if idx + 1 < len(args) else None
        limit = 500
        if "--limit" in args:
            idx = args.index("--limit")
            try:
                limit = int(args[idx + 1]) if idx + 1 < len(args) else 500
            except (ValueError, IndexError):
                limit = 500
        since = None
        if "--since" in args:
            idx = args.index("--since")
            since = args[idx + 1] if idx + 1 < len(args) else None
        output_file = None
        if "--output" in args:
            idx = args.index("--output")
            output_file = args[idx + 1] if idx + 1 < len(args) else None

        try:
            result = compute_knowledge_export(fmt=fmt, category=category, tag=tag, limit=limit, since=since)
        except Exception as exc:
            print(f"⚠ export failed: {exc}", file=sys.stderr)
            return

        if fmt == "markdown":
            text = _format_export_markdown(result)
        elif fmt == "csv":
            text = _format_export_csv(result)
        else:
            text = _format_export_json(result)

        if output_file:
            try:
                Path(output_file).write_text(text, encoding="utf-8")
                print(f"✅ Exported {result['count']} entr(ies) to {output_file}")
            except OSError as exc:
                print(f"⚠ Could not write {output_file}: {exc}", file=sys.stderr)
        else:
            try:
                print(text)
            except UnicodeEncodeError:
                sys.stdout.buffer.write(text.encode("utf-8"))
                sys.stdout.buffer.write(b"\n")
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

    if "--freshness" in args:
        days = 90
        limit = 50
        if "--days" in args:
            idx = args.index("--days")
            days = int(args[idx + 1]) if idx + 1 < len(args) else 90
        if "--limit" in args:
            idx = args.index("--limit")
            limit = int(args[idx + 1]) if idx + 1 < len(args) else 50
        if not DB_PATH.exists():
            if "--json" in args:
                print(json.dumps({"days_threshold": days, "count": 0, "entries": []}, indent=2))
            else:
                print(f"📅 Stale entries (not updated in >{days} days): 0")
                print("  ✅ No knowledge database found (fresh install).")
            return
        try:
            db = get_db()
            rows = db.execute(
                """
                SELECT id, category, title, last_seen, confidence
                FROM knowledge_entries
                WHERE datetime(last_seen) < datetime('now', ? || ' days')
                  AND (deleted_at IS NULL OR deleted_at = '')
                ORDER BY last_seen ASC
                LIMIT ?
                """,
                (f"-{days}", limit),
            ).fetchall()
            now = time.time()
            entries = []
            for r in rows:
                try:
                    from datetime import timezone as _tz

                    ls = r[3] or ""
                    if ls:
                        from datetime import datetime as _dt

                        dt = _dt.fromisoformat(ls.replace("Z", "+00:00"))
                        age_days = int((time.time() - dt.timestamp()) / 86400)
                    else:
                        age_days = -1
                except Exception:
                    age_days = -1
                entries.append(
                    {
                        "id": r[0],
                        "category": r[1],
                        "title": r[2],
                        "last_seen": r[3],
                        "days_old": age_days,
                        "confidence": r[4],
                    }
                )
            if "--json" in args:
                print(
                    json.dumps(
                        {"days_threshold": days, "count": len(entries), "entries": entries},
                        indent=2,
                        ensure_ascii=False,
                    )
                )
            else:
                print(f"📅 Stale entries (not updated in >{days} days): {len(entries)}")
                for e in entries:
                    last = (e["last_seen"] or "never")[:10]
                    print(f"  #{e['id']:6d}  [{e['category']:12s}]  {e['title'][:55]:<55}  {last}  ({e['days_old']}d)")
                if not entries:
                    print(f"  ✅ No entries older than {days} days.")
        except Exception as exc:
            print(f"⚠ freshness check failed: {exc}", file=sys.stderr)
        return

    if "--decay-confidence" in args:
        if "--preview" in args:
            limit = 20
            half_life = 30.0
            if "--limit" in args:
                idx = args.index("--limit")
                limit = int(args[idx + 1]) if idx + 1 < len(args) else 20
            if "--half-life" in args:
                idx = args.index("--half-life")
                half_life = float(args[idx + 1]) if idx + 1 < len(args) else 30.0
            result = compute_decay_preview(limit=limit, half_life_days=half_life)
            if "--json" in args:
                print(json.dumps(result, indent=2, ensure_ascii=False))
            else:
                print(format_decay_preview(result))
            return
        stale_days = 90
        decay_rate = 0.05
        if "--stale" in args:
            idx = args.index("--stale")
            stale_days = int(args[idx + 1]) if idx + 1 < len(args) else 90
        if "--decay-rate" in args:
            idx = args.index("--decay-rate")
            decay_rate = float(args[idx + 1]) if idx + 1 < len(args) else 0.05
        result = compute_confidence_decay(stale_days=stale_days, decay_rate=decay_rate)
        if "--json" in args:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            count = result["decayed_count"]
            print(f"Confidence decay applied: {count} entries updated (stale_days={stale_days}, rate={decay_rate})")
        return

    if "--evict-candidates" in args:
        limit = 20
        if "--limit" in args:
            idx = args.index("--limit")
            limit = int(args[idx + 1]) if idx + 1 < len(args) else 20
        result = compute_eviction_candidates(limit=limit)
        if "--json" in args:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            candidates = result["candidates"]
            print(f"Eviction candidates ({len(candidates)}):")
            for c in candidates:
                print(
                    f"  [{c['id']}] {c['title'][:60]}  score={c['eviction_score']:.6f}  conf={c['confidence']}  age={c['age_days']}d"
                )
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

    if args and args[0] == "bulk-tag":
        cmd_bulk_tag(args[1:])
        return

    if args and args[0] == "pin":
        if len(args) < 2:
            print("Usage: knowledge-health.py pin <id>", file=sys.stderr)
            sys.exit(1)
        cmd_pin(args[1])
        return

    if args and args[0] == "unpin":
        if len(args) < 2:
            print("Usage: knowledge-health.py unpin <id>", file=sys.stderr)
            sys.exit(1)
        cmd_unpin(args[1])
        return

    if args and args[0] == "pins":
        cmd_pins()
        return

    stale_days = 30
    if "--stale" in args:
        idx = args.index("--stale")
        stale_days = int(args[idx + 1]) if idx + 1 < len(args) else 30

    health = compute_health(stale_days=stale_days)
    if health.get("stale_count", 0) > 0:
        _emit_knowledge_event_fail_open(
            "knowledge_decayed",
            {
                "stale_count": health.get("stale_count", 0),
                "stale_pct": health.get("stale_pct", 0.0),
                "stale_days": stale_days,
                "score": health.get("score", 0.0),
            },
        )

    if "--score" in args:
        print(health["score"])
    elif "--json" in args:
        print(json.dumps(health, indent=2, ensure_ascii=False))
    else:
        print(format_report(health))


def cmd_pin(entry_id: str) -> None:
    """Pin an entry by id prefix: set priority='P0'."""
    try:
        db = get_db()
        cur = db.execute(
            "UPDATE knowledge_entries SET priority='P0' WHERE id LIKE ?",
            (entry_id + "%",),
        )
        db.commit()
        count = cur.rowcount
        db.close()
        if count == 0:
            print(f"⚠ No entry found matching id prefix '{entry_id}'.", file=sys.stderr)
        else:
            print(f"📌 Pinned {count} entr(ies) matching '{entry_id}' → priority=P0")
    except Exception as exc:
        print(f"⚠ pin failed: {exc}", file=sys.stderr)


def cmd_unpin(entry_id: str) -> None:
    """Unpin an entry by id prefix: set priority='P2'."""
    try:
        db = get_db()
        cur = db.execute(
            "UPDATE knowledge_entries SET priority='P2' WHERE id LIKE ?",
            (entry_id + "%",),
        )
        db.commit()
        count = cur.rowcount
        db.close()
        if count == 0:
            print(f"⚠ No entry found matching id prefix '{entry_id}'.", file=sys.stderr)
        else:
            print(f"🔓 Unpinned {count} entr(ies) matching '{entry_id}' → priority=P2")
    except Exception as exc:
        print(f"⚠ unpin failed: {exc}", file=sys.stderr)


def cmd_pins() -> None:
    """List all P0 pinned knowledge entries."""
    try:
        db = get_db()
        rows = db.execute(
            "SELECT id, title, category, priority, created_at FROM knowledge_entries"
            " WHERE priority='P0' ORDER BY created_at DESC"
        ).fetchall()
        db.close()
        if not rows:
            print("📌 No pinned (P0) entries.")
            return
        print(f"📌 Pinned entries (P0) — {len(rows)} total:\n")
        col_id = max(len("ID"), max(len(str(r["id"])) for r in rows))
        col_cat = max(len("category"), max(len(str(r["category"] or "")) for r in rows))
        col_date = 10
        header = f"  {'ID':<{col_id}}  {'category':<{col_cat}}  {'created':<{col_date}}  title"
        print(header)
        print("  " + "-" * (len(header) - 2))
        for r in rows:
            title = (r["title"] or "")[:60]
            cat = r["category"] or ""
            date = (r["created_at"] or "")[:10]
            print(f"  {str(r['id']):<{col_id}}  {cat:<{col_cat}}  {date:<{col_date}}  {title}")
    except Exception as exc:
        print(f"⚠ pins failed: {exc}", file=sys.stderr)


def cmd_bulk_tag(args: list) -> None:
    """Bulk-tag knowledge entries by selector + mutation.

    Selectors (at least one required):
      --query TEXT    Substring match on title+content
      --wing W        Filter by wing
      --room R        Filter by room
      --tag T         Filter by existing tag

    Mutations (at least one required):
      --add-tag TAG   Add tag to matched entries
      --set-wing W    Set wing on matched entries
      --set-room R    Set room on matched entries

    Flags:
      --apply         Commit changes (dry-run by default)
    """
    import datetime as _dt_bt

    query_text = None
    wing = None
    room = None
    tag = None
    add_tag = None
    set_wing = None
    set_room = None
    apply_flag = "--apply" in args

    if "--query" in args:
        idx = args.index("--query")
        query_text = args[idx + 1] if idx + 1 < len(args) else None
    if "--wing" in args:
        idx = args.index("--wing")
        wing = args[idx + 1] if idx + 1 < len(args) else None
    if "--room" in args:
        idx = args.index("--room")
        room = args[idx + 1] if idx + 1 < len(args) else None
    if "--tag" in args:
        idx = args.index("--tag")
        tag = args[idx + 1] if idx + 1 < len(args) else None
    if "--add-tag" in args:
        idx = args.index("--add-tag")
        add_tag = args[idx + 1] if idx + 1 < len(args) else None
    if "--set-wing" in args:
        idx = args.index("--set-wing")
        set_wing = args[idx + 1] if idx + 1 < len(args) else None
    if "--set-room" in args:
        idx = args.index("--set-room")
        set_room = args[idx + 1] if idx + 1 < len(args) else None

    has_selector = any(v is not None for v in [query_text, wing, room, tag])
    has_mutation = any(v is not None for v in [add_tag, set_wing, set_room])

    if not has_selector:
        print(
            "⚠ bulk-tag requires at least one selector (--query, --wing, --room, --tag).",
            file=sys.stderr,
        )
        sys.exit(1)
    if not has_mutation:
        print(
            "⚠ bulk-tag requires at least one mutation (--add-tag, --set-wing, --set-room).",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        db = get_db()
        ke_cols = {r[1] for r in db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
        deleted_at_clause = "ke.deleted_at IS NULL" if "deleted_at" in ke_cols else ""

        base = "SELECT DISTINCT ke.id FROM knowledge_entries ke"
        where: list[str] = [deleted_at_clause] if deleted_at_clause else []
        params: list = []

        if tag:
            base += " JOIN entry_concept_tags ect ON ke.id = ect.entry_id"
            where.append("ect.tag = ?")
            params.append(tag)
        if wing:
            where.append("ke.wing = ?")
            params.append(wing)
        if room:
            where.append("ke.room = ?")
            params.append(room)
        if query_text:
            where.append("(ke.title LIKE ? ESCAPE '\\' OR ke.content LIKE ? ESCAPE '\\')")
            escaped = query_text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like_val = "%" + escaped + "%"
            params.extend([like_val, like_val])

        sel_query = base + (" WHERE " + " AND ".join(where) if where else "")
        matched_ids = [r[0] for r in db.execute(sel_query, params).fetchall()]
        count = len(matched_ids)

        print(f"{count} entr(ies) would be affected.")
        if not apply_flag:
            print("(Dry-run — pass --apply to commit changes.)")
            db.close()
            return

        if count == 0:
            print("Nothing to update.")
            db.close()
            return

        now_str = _dt_bt.datetime.utcnow().isoformat()
        if set_wing is not None:
            for eid in matched_ids:
                db.execute(
                    "UPDATE knowledge_entries SET wing=? WHERE id=?",
                    (set_wing, eid),
                )
        if set_room is not None:
            for eid in matched_ids:
                db.execute(
                    "UPDATE knowledge_entries SET room=? WHERE id=?",
                    (set_room, eid),
                )
        if add_tag is not None:
            for eid in matched_ids:
                existing = db.execute(
                    "SELECT 1 FROM entry_concept_tags WHERE entry_id=? AND tag=?",
                    (eid, add_tag),
                ).fetchone()
                if not existing:
                    db.execute(
                        "INSERT INTO entry_concept_tags (entry_id, tag, source, tagged_at) VALUES (?,?,?,?)",
                        (eid, add_tag, "bulk-tag", now_str),
                    )

        db.commit()
        db.close()
        print(f"✅ Applied to {count} entr(ies).")
    except Exception as exc:
        try:
            db.close()
        except Exception:
            pass
        print(f"⚠ bulk-tag failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
