#!/usr/bin/env python3
"""
dream.py â€” Dream-score ranking for knowledge promotion (issue #159).

Computes a weighted dream-score for each knowledge entry using recall
telemetry (entry_recall_stats) and concept tags (entry_concept_tags).
Entries that pass configurable gates are candidates for promotion.

Usage:
    python dream.py                     # Score all entries, persist, show top-20
    python dream.py --dry-run           # Score and list top-N without persisting
    python dream.py --top 10            # Show top-N candidates
    python dream.py --json              # JSON output
    python dream.py --min-score 0.75    # Override gate min score
    python dream.py --min-recall 3      # Override gate min recall count
    python dream.py --min-queries 2     # Override gate min unique queries

    # Override weights (if they sum > 1.0, they are automatically renormalized to sum=1.0)
    python dream.py --w-frequency 0.24 --w-relevance 0.30 --w-diversity 0.15 \
                    --w-recency 0.15 --w-consolidation 0.10 --w-conceptual 0.06

Score formula (6 weighted signals, each normalized to [0, 1]):
    dream_score = w_frequency   * signal_frequency
                + w_relevance   * signal_relevance
                + w_diversity   * signal_diversity
                + w_recency     * signal_recency
                + w_consolidation * signal_consolidation
                + w_conceptual  * signal_conceptual

Gate: entry passes if dream_score >= min_score
                  AND recall_count >= min_recall
                  AND unique_queries >= min_queries

Default weights (from OpenClaw extensions/memory-core/src/short-term-promotion.ts):
    frequency=0.24, relevance=0.30, diversity=0.15,
    recency=0.15, consolidation=0.10, conceptual=0.06

Default gates:
    min_score=0.75, min_recall=3, min_queries=2
"""

import argparse
import json
import math
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

SESSION_STATE = Path.home() / ".copilot" / "session-state"
DB_PATH = SESSION_STATE / "knowledge.db"

# Default weights (must sum â‰¤ 1.0)
DEFAULT_WEIGHTS = {
    "frequency": 0.24,
    "relevance": 0.30,
    "diversity": 0.15,
    "recency": 0.15,
    "consolidation": 0.10,
    "conceptual": 0.06,
}

# Default gate thresholds
DEFAULT_MIN_SCORE = 0.75
DEFAULT_MIN_RECALL = 3
DEFAULT_MIN_QUERIES = 2

# Normalization caps
_RECALL_CAP = 100.0  # recall_count above this â†’ saturates at 1.0
_QUERIES_CAP = 50.0  # unique_queries above this â†’ saturates at 1.0
_TAGS_CAP = 10.0  # tag count above this â†’ saturates at 1.0
_RECENCY_HALFLIFE_DAYS = 30.0  # recency half-life: 30 days â†’ signal 0.5


def _open_db(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        print(f"dream: database not found: {db_path}", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_dream_scores_table(conn: sqlite3.Connection) -> None:
    """Create entry_dream_scores if it doesn't exist (idempotent)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS entry_dream_scores (
            entry_id INTEGER PRIMARY KEY,
            score REAL NOT NULL DEFAULT 0.0,
            signal_frequency REAL DEFAULT 0.0,
            signal_relevance REAL DEFAULT 0.0,
            signal_diversity REAL DEFAULT 0.0,
            signal_recency REAL DEFAULT 0.0,
            signal_consolidation REAL DEFAULT 0.0,
            signal_conceptual REAL DEFAULT 0.0,
            passes_gate INTEGER NOT NULL DEFAULT 0,
            scored_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_eds_score ON entry_dream_scores(score DESC);
        CREATE INDEX IF NOT EXISTS idx_eds_passes_gate ON entry_dream_scores(passes_gate);
    """)
    conn.commit()


def _days_since(iso_str: str | None) -> float:
    """Return days elapsed since iso_str (UTC), or very large number if missing.

    Naive timestamps (no timezone suffix, e.g. written by briefing.py as
    ``YYYY-MM-DDTHH:MM:SS``) are treated as UTC so they are not silently zeroed.
    """
    if not iso_str:
        return 9999.0
    try:
        ts = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            # briefing.py writes timestamps without a timezone suffix; treat as UTC.
            ts = ts.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        diff = now - ts
        return max(0.0, diff.total_seconds() / 86400.0)
    except (ValueError, TypeError):
        return 9999.0


def _sig_recency(days: float) -> float:
    """Exponential decay: 1.0 at 0 days, 0.5 at half-life days."""
    return math.exp(-math.log(2.0) * days / _RECENCY_HALFLIFE_DAYS)


def _normalize(value: float, cap: float) -> float:
    """Linear normalization capped at cap, result in [0, 1]."""
    return min(1.0, max(0.0, value / cap)) if cap > 0 else 0.0


def compute_score(
    recall_count: int,
    recall_days: int,
    unique_queries: int,
    last_recalled_at: str | None,
    tag_count: int,
    confidence: float,
    occurrence_count: int,
    weights: dict,
) -> dict:
    """
    Compute dream-score signals and weighted total.

    Returns a dict with signal_* floats and 'score'.
    """
    sig_freq = _normalize(recall_count, _RECALL_CAP)
    sig_rel = _normalize(unique_queries, _QUERIES_CAP)
    sig_div = _normalize(tag_count, _TAGS_CAP)
    sig_rec = _sig_recency(_days_since(last_recalled_at))
    # consolidation: confidence * capped recurrence
    sig_con = float(confidence or 0.0) * _normalize(occurrence_count, 20.0)
    sig_con = min(1.0, max(0.0, sig_con))
    sig_cpt = 1.0 if tag_count > 0 else 0.0

    w = weights
    score = (
        w["frequency"] * sig_freq
        + w["relevance"] * sig_rel
        + w["diversity"] * sig_div
        + w["recency"] * sig_rec
        + w["consolidation"] * sig_con
        + w["conceptual"] * sig_cpt
    )
    return {
        "signal_frequency": round(sig_freq, 6),
        "signal_relevance": round(sig_rel, 6),
        "signal_diversity": round(sig_div, 6),
        "signal_recency": round(sig_rec, 6),
        "signal_consolidation": round(sig_con, 6),
        "signal_conceptual": round(sig_cpt, 6),
        "score": round(score, 6),
    }


def fetch_entries(conn: sqlite3.Connection) -> list[dict]:
    """Fetch all knowledge entries joined with recall stats and tag counts."""
    cur = conn.execute("""
        SELECT
            ke.id              AS entry_id,
            ke.title           AS title,
            ke.category        AS category,
            ke.confidence      AS confidence,
            ke.occurrence_count AS occurrence_count,
            COALESCE(ers.recall_count,   0) AS recall_count,
            COALESCE(ers.recall_days,    0) AS recall_days,
            COALESCE(ers.unique_queries, 0) AS unique_queries,
            ers.last_recalled_at            AS last_recalled_at,
            COALESCE(tc.tag_count,       0) AS tag_count
        FROM knowledge_entries ke
        LEFT JOIN entry_recall_stats ers ON ers.entry_id = ke.id
        LEFT JOIN (
            SELECT entry_id, COUNT(*) AS tag_count
            FROM entry_concept_tags
            GROUP BY entry_id
        ) tc ON tc.entry_id = ke.id
    """)
    cols = [desc[0] for desc in cur.description]
    return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]


def run_scoring(
    conn: sqlite3.Connection,
    weights: dict,
    min_score: float,
    min_recall: int,
    min_queries: int,
    dry_run: bool,
    top_n: int,
    as_json: bool,
) -> int:
    """Run dream-score computation, persist (unless dry_run), and report."""
    _ensure_dream_scores_table(conn)
    entries = fetch_entries(conn)

    results = []
    for entry in entries:
        sig = compute_score(
            recall_count=int(entry["recall_count"] or 0),
            recall_days=int(entry["recall_days"] or 0),
            unique_queries=int(entry["unique_queries"] or 0),
            last_recalled_at=entry.get("last_recalled_at"),
            tag_count=int(entry["tag_count"] or 0),
            confidence=float(entry["confidence"] or 1.0),
            occurrence_count=int(entry["occurrence_count"] or 1),
            weights=weights,
        )
        passes_gate = (
            sig["score"] >= min_score
            and int(entry["recall_count"] or 0) >= min_recall
            and int(entry["unique_queries"] or 0) >= min_queries
        )
        rec = {
            "entry_id": entry["entry_id"],
            "title": entry["title"],
            "category": entry["category"],
            "recall_count": int(entry["recall_count"] or 0),
            "unique_queries": int(entry["unique_queries"] or 0),
            "tag_count": int(entry["tag_count"] or 0),
            "passes_gate": passes_gate,
            **sig,
        }
        results.append(rec)

    # Sort by score descending
    results.sort(key=lambda r: r["score"], reverse=True)

    # Persist unless dry-run
    if not dry_run:
        now_str = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        conn.executemany(
            """
            INSERT INTO entry_dream_scores
                (entry_id, score,
                 signal_frequency, signal_relevance, signal_diversity,
                 signal_recency, signal_consolidation, signal_conceptual,
                 passes_gate, scored_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entry_id) DO UPDATE SET
                score = excluded.score,
                signal_frequency = excluded.signal_frequency,
                signal_relevance = excluded.signal_relevance,
                signal_diversity = excluded.signal_diversity,
                signal_recency = excluded.signal_recency,
                signal_consolidation = excluded.signal_consolidation,
                signal_conceptual = excluded.signal_conceptual,
                passes_gate = excluded.passes_gate,
                scored_at = excluded.scored_at
            """,
            [
                (
                    r["entry_id"],
                    r["score"],
                    r["signal_frequency"],
                    r["signal_relevance"],
                    r["signal_diversity"],
                    r["signal_recency"],
                    r["signal_consolidation"],
                    r["signal_conceptual"],
                    1 if r["passes_gate"] else 0,
                    now_str,
                )
                for r in results
            ],
        )
        conn.commit()

    # Top-N slice
    top = results[:top_n]
    candidates = [r for r in results if r["passes_gate"]]
    gate_count = len(candidates)

    if as_json:
        output = {
            "total_entries": len(results),
            "gate_count": gate_count,
            "dry_run": dry_run,
            "weights": weights,
            "gates": {
                "min_score": min_score,
                "min_recall": min_recall,
                "min_queries": min_queries,
            },
            "top": top,
        }
        print(json.dumps(output, indent=2))
        return 0

    # Human output
    mode = "[DRY RUN â€” not persisted]" if dry_run else "[persisted]"
    print(f"Dream Score Report {mode}")
    print(f"  Entries scored  : {len(results)}")
    print(f"  Gate candidates : {gate_count}  (score>={min_score}, recall>={min_recall}, queries>={min_queries})")
    print(f"  Showing top     : {min(top_n, len(results))}")
    print()
    if not top:
        print("  (no entries)")
        return 0
    header = f"  {'#':>3}  {'Score':>6}  {'Rc':>4}  {'Uq':>4}  {'Tg':>4}  {'Gate':>5}  Title"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for i, r in enumerate(top, 1):
        gate_mark = "âœ“" if r["passes_gate"] else " "
        title = (r["title"] or "")[:60]
        print(
            f"  {i:>3}.  {r['score']:>6.3f}  {r['recall_count']:>4}  "
            f"{r['unique_queries']:>4}  {r['tag_count']:>4}  {gate_mark:>5}  {title}"
        )
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="dream",
        description="Dream-score ranking for knowledge promotion (issue #159).",
    )
    p.add_argument("--dry-run", action="store_true", help="List top-N candidates without persisting scores to DB.")
    p.add_argument("--top", type=int, default=20, metavar="N", help="Show top-N entries (default: 20).")
    p.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON output.")
    p.add_argument(
        "--db",
        type=str,
        default=None,
        metavar="PATH",
        help="Path to knowledge.db (default: ~/.copilot/session-state/knowledge.db).",
    )

    # Gate overrides
    g = p.add_argument_group("gate thresholds")
    g.add_argument(
        "--min-score",
        type=float,
        default=DEFAULT_MIN_SCORE,
        metavar="SCORE",
        help=f"Min dream score to pass gate (default: {DEFAULT_MIN_SCORE}).",
    )
    g.add_argument(
        "--min-recall",
        type=int,
        default=DEFAULT_MIN_RECALL,
        metavar="N",
        help=f"Min recall count to pass gate (default: {DEFAULT_MIN_RECALL}).",
    )
    g.add_argument(
        "--min-queries",
        type=int,
        default=DEFAULT_MIN_QUERIES,
        metavar="N",
        help=f"Min unique queries to pass gate (default: {DEFAULT_MIN_QUERIES}).",
    )

    # Weight overrides
    w = p.add_argument_group("weight overrides")
    w.add_argument(
        "--w-frequency",
        type=float,
        default=DEFAULT_WEIGHTS["frequency"],
        metavar="W",
        help=f"Frequency weight (default: {DEFAULT_WEIGHTS['frequency']}).",
    )
    w.add_argument(
        "--w-relevance",
        type=float,
        default=DEFAULT_WEIGHTS["relevance"],
        metavar="W",
        help=f"Relevance weight (default: {DEFAULT_WEIGHTS['relevance']}).",
    )
    w.add_argument(
        "--w-diversity",
        type=float,
        default=DEFAULT_WEIGHTS["diversity"],
        metavar="W",
        help=f"Diversity weight (default: {DEFAULT_WEIGHTS['diversity']}).",
    )
    w.add_argument(
        "--w-recency",
        type=float,
        default=DEFAULT_WEIGHTS["recency"],
        metavar="W",
        help=f"Recency weight (default: {DEFAULT_WEIGHTS['recency']}).",
    )
    w.add_argument(
        "--w-consolidation",
        type=float,
        default=DEFAULT_WEIGHTS["consolidation"],
        metavar="W",
        help=f"Consolidation weight (default: {DEFAULT_WEIGHTS['consolidation']}).",
    )
    w.add_argument(
        "--w-conceptual",
        type=float,
        default=DEFAULT_WEIGHTS["conceptual"],
        metavar="W",
        help=f"Conceptual weight (default: {DEFAULT_WEIGHTS['conceptual']}).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    db_path = Path(args.db) if args.db else DB_PATH

    weights = {
        "frequency": args.w_frequency,
        "relevance": args.w_relevance,
        "diversity": args.w_diversity,
        "recency": args.w_recency,
        "consolidation": args.w_consolidation,
        "conceptual": args.w_conceptual,
    }

    # Validate weights: each must be non-negative; renormalize if sum > 1.0
    for k, v in weights.items():
        if v < 0.0:
            print(f"dream: weight --w-{k} must be >= 0 (got {v})", file=sys.stderr)
            return 2
    total_w = sum(weights.values())
    if total_w > 1.0 + 1e-9:
        weights = {k: v / total_w for k, v in weights.items()}
        print(
            f"dream: warning: weights summed to {total_w:.4f} > 1.0; renormalized to sum=1.0",
            file=sys.stderr,
        )
        total_w = 1.0

    # Warn when the gate is structurally unreachable: maximum achievable score
    # equals sum(weights) because each signal is in [0, 1].  If that ceiling is
    # below min_score, gate_count will always be zero with no other indication.
    if total_w < args.min_score - 1e-9:
        print(
            f"dream: warning: weights sum to {total_w:.4f}, which is below --min-score "
            f"{args.min_score:.4f}; the maximum achievable dream score is {total_w:.4f} "
            f"so no entry can ever pass the score gate. "
            f"Lower --min-score or increase the weights.",
            file=sys.stderr,
        )

    if args.top < 1:
        print("dream: --top must be >= 1", file=sys.stderr)
        return 2

    conn = _open_db(db_path)
    try:
        return run_scoring(
            conn=conn,
            weights=weights,
            min_score=args.min_score,
            min_recall=args.min_recall,
            min_queries=args.min_queries,
            dry_run=args.dry_run,
            top_n=args.top,
            as_json=args.as_json,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
