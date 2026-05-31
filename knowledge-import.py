#!/usr/bin/env python3
"""
knowledge-import.py — Import knowledge entries from another project's knowledge.db

Selectively imports entries that are relevant to the local project, filtered by
semantic similarity (TF-IDF fallback when no embedding API key is configured).

Usage:
    python knowledge-import.py --from /path/to/other/knowledge.db
        Show stats about what would be imported.

    python knowledge-import.py --from /path/to/other/knowledge.db --dry-run
        Print each candidate entry with similarity scores; no DB writes.

    python knowledge-import.py --from /path/to/other/knowledge.db \\
        --tags "shared,patterns" --min-confidence 0.7
        Filtered import: only entries matching tags and confidence floor.

    python knowledge-import.py --from /path/to/other/knowledge.db \\
        --categories "mistake,pattern" --similarity 0.80 --limit 30 --json
        JSON output, custom thresholds.

Similarity pipeline:
  1. Load source entries (apply --categories / --tags / --min-confidence / --limit)
  2. Dedup by (category, title) against local DB — skip existing
  3. Compute best cosine similarity to any local entry (embeddings or TF-IDF)
  4. Skip if best_sim > 0.9  (near-duplicate already in local DB)
  5. Skip if best_sim < --similarity  (not relevant enough to this project)
     Exception: when local DB has < 3 entries, similarity filter is bypassed.
  6. Insert accepted entries with `imported_from:<source_path_hash>` tag
     and confidence discounted by ×0.85 (provenance discount).

Deduplication:
    Title + category uniqueness is checked against the local DB.
    Entries that already exist (same title+category) are always skipped.
"""

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import struct
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SESSION_STATE = Path.home() / ".copilot" / "session-state"
DB_PATH = Path(os.environ.get("SK_DB_PATH", str(SESSION_STATE / "knowledge.db"))).expanduser()

PROVENANCE_DISCOUNT = 0.85
NEAR_DUP_THRESHOLD = 0.90
IMPORT_SESSION_ID = "knowledge-import"

# ---------------------------------------------------------------------------
# Pure-stdlib TF-IDF similarity
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    """Lowercase word tokens, length ≥ 2."""
    return re.findall(r"\b[a-z]{2,}\b", text.lower())


def _build_tfidf_index(docs: list[str]) -> tuple[list[dict], dict]:
    """Return (normalized TF-IDF vectors, idf_weights) for a corpus."""
    n = len(docs)
    if n == 0:
        return [], {}
    tf_lists = [Counter(_tokenize(d)) for d in docs]
    df: Counter = Counter()
    for tf in tf_lists:
        df.update(tf.keys())
    idf = {term: math.log((n + 1) / (df[term] + 1)) + 1 for term in df}
    vectors = []
    for tf in tf_lists:
        if not tf:
            vectors.append({})
            continue
        max_tf = max(tf.values())
        vec = {t: (cnt / max_tf) * idf[t] for t, cnt in tf.items() if t in idf}
        norm = math.sqrt(sum(v * v for v in vec.values()))
        if norm > 0:
            vec = {t: v / norm for t, v in vec.items()}
        vectors.append(vec)
    return vectors, idf


def _query_vector(text: str, idf: dict) -> dict:
    """TF-IDF vector for *text* using pre-built IDF weights."""
    tf = Counter(_tokenize(text))
    if not tf:
        return {}
    max_tf = max(tf.values())
    vec = {t: (cnt / max_tf) * idf[t] for t, cnt in tf.items() if t in idf}
    norm = math.sqrt(sum(v * v for v in vec.values()))
    if norm > 0:
        vec = {t: v / norm for t, v in vec.items()}
    return vec


def _cosine_sparse(a: dict, b: dict) -> float:
    """Cosine similarity between two pre-normalized sparse vectors."""
    return float(min(1.0, max(0.0, sum(a.get(t, 0.0) * v for t, v in b.items()))))


def _best_tfidf_sim(query_text: str, corpus_vecs: list[dict], idf: dict) -> float:
    if not corpus_vecs:
        return 0.0
    qv = _query_vector(query_text, idf)
    if not qv:
        return 0.0
    return max(_cosine_sparse(qv, cv) for cv in corpus_vecs)


# ---------------------------------------------------------------------------
# Embedding-based similarity (uses stored BLOB vectors)
# ---------------------------------------------------------------------------


def _unpack_vector(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def _cosine_dense(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return min(1.0, max(0.0, dot / (norm_a * norm_b)))


def _load_embeddings(db: sqlite3.Connection) -> dict[int, list[float]]:
    """Return {knowledge_entry_id: vector} for all stored embeddings."""
    try:
        rows = db.execute("SELECT source_id, vector FROM embeddings WHERE source_type = 'knowledge_entries'").fetchall()
        return {row[0]: _unpack_vector(row[1]) for row in rows if row[1]}
    except sqlite3.OperationalError:
        return {}


def _best_dense_sim(vec: list[float], corpus_vecs: list[list[float]]) -> float:
    if not corpus_vecs:
        return 0.0
    return max(_cosine_dense(vec, cv) for cv in corpus_vecs)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _open_source_db(path: Path) -> sqlite3.Connection:
    """Open source DB read-only."""
    uri = f"{path.as_uri()}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True, check_same_thread=False)
    except sqlite3.OperationalError as exc:
        raise SystemExit(f"Cannot open source DB '{path}': {exc}") from exc
    con.row_factory = sqlite3.Row
    return con


def _open_local_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=15000")
    con.row_factory = sqlite3.Row
    return con


def _has_table(db: sqlite3.Connection, name: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _local_entry_exists(local_db: sqlite3.Connection, category: str, title: str) -> bool:
    row = local_db.execute(
        "SELECT 1 FROM knowledge_entries WHERE category = ? AND title = ?",
        (category, title),
    ).fetchone()
    return row is not None


def _load_source_entries(
    src_db: sqlite3.Connection,
    *,
    categories: list[str] | None,
    min_confidence: float,
    tag_filter: list[str],
    limit: int,
) -> list[dict]:
    """Load entries from source DB applying optional filters."""
    if not _has_table(src_db, "knowledge_entries"):
        return []

    clauses: list[str] = ["confidence >= ?"]
    params: list = [min_confidence]

    if categories:
        placeholders = ",".join("?" for _ in categories)
        clauses.append(f"category IN ({placeholders})")
        params.extend(categories)

    where = " AND ".join(clauses)
    query = f"SELECT * FROM knowledge_entries WHERE {where} ORDER BY confidence DESC, last_seen DESC"
    if limit > 0:
        query += " LIMIT ?"
        params.append(limit)

    rows = src_db.execute(query, params).fetchall()
    entries = [dict(row) for row in rows]

    if tag_filter:
        result = []
        for e in entries:
            etags = {t.strip().lower() for t in (e.get("tags") or "").split(",") if t.strip()}
            if any(tf.lower() in etags for tf in tag_filter):
                result.append(e)
        return result
    return entries


def _load_local_texts(local_db: sqlite3.Connection) -> list[str]:
    """Return combined title+content texts for all local entries."""
    if not _has_table(local_db, "knowledge_entries"):
        return []
    rows = local_db.execute("SELECT title, content FROM knowledge_entries").fetchall()
    return [f"{r[0]} {r[1]}" for r in rows]


def _local_entry_ids(local_db: sqlite3.Connection) -> list[int]:
    if not _has_table(local_db, "knowledge_entries"):
        return []
    rows = local_db.execute("SELECT id FROM knowledge_entries").fetchall()
    return [r[0] for r in rows]


def _stable_id(category: str, title: str, content: str) -> str:
    payload = f"{category}\0{title}\0{content}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _merge_tags(existing: str, *additions: str) -> str:
    parts = [t.strip() for t in existing.split(",") if t.strip()]
    for add in additions:
        for t in add.split(","):
            t = t.strip()
            if t and t not in parts:
                parts.append(t)
    return ",".join(parts)


def _source_tag(source_path: Path) -> str:
    h = hashlib.sha256(str(source_path).encode()).hexdigest()[:8]
    return f"imported_from:{h}"


def _insert_entry(
    local_db: sqlite3.Connection,
    entry: dict,
    extra_tag: str,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    category = entry.get("category", "discovery")
    title = entry.get("title", "")
    content = entry.get("content", "")
    original_conf = float(entry.get("confidence") or 1.0)
    confidence = max(0.0, min(1.0, original_conf * PROVENANCE_DISCOUNT))
    tags = _merge_tags(entry.get("tags") or "", extra_tag)
    session_id = IMPORT_SESSION_ID

    # Check which columns the local DB has
    ke_cols = {row[1] for row in local_db.execute("PRAGMA table_info(knowledge_entries)").fetchall()}
    has_stable_id = "stable_id" in ke_cols
    has_priority = "priority" in ke_cols

    if has_stable_id:
        sid = _stable_id(category, title, content)
        if has_priority:
            local_db.execute(
                """
                INSERT OR IGNORE INTO knowledge_entries
                    (category, title, stable_id, content, tags, confidence, session_id,
                     occurrence_count, first_seen, last_seen, wing, room, priority)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                """,
                (
                    category,
                    title,
                    sid,
                    content,
                    tags,
                    confidence,
                    session_id,
                    now,
                    now,
                    entry.get("wing") or "",
                    entry.get("room") or "",
                    entry.get("priority") or "P2",
                ),
            )
        else:
            local_db.execute(
                """
                INSERT OR IGNORE INTO knowledge_entries
                    (category, title, stable_id, content, tags, confidence, session_id,
                     occurrence_count, first_seen, last_seen, wing, room)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                """,
                (
                    category,
                    title,
                    sid,
                    content,
                    tags,
                    confidence,
                    session_id,
                    now,
                    now,
                    entry.get("wing") or "",
                    entry.get("room") or "",
                ),
            )
    else:
        local_db.execute(
            """
            INSERT OR IGNORE INTO knowledge_entries
                (category, title, content, tags, confidence, session_id,
                 occurrence_count, first_seen, last_seen, wing, room)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
            """,
            (
                category,
                title,
                content,
                tags,
                confidence,
                session_id,
                now,
                now,
                entry.get("wing") or "",
                entry.get("room") or "",
            ),
        )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def _build_similarity_index(
    local_db: sqlite3.Connection,
) -> tuple[str, list, dict]:
    """
    Return (mode, corpus_data, idf_or_empty):
      - mode == 'embedding': corpus_data is list[list[float]]
      - mode == 'tfidf':     corpus_data is list[dict], idf is dict
    """
    local_embs = _load_embeddings(local_db)
    if local_embs:
        return "embedding", list(local_embs.values()), {}

    local_texts = _load_local_texts(local_db)
    if not local_texts:
        return "tfidf", [], {}

    vectors, idf = _build_tfidf_index(local_texts)
    return "tfidf", vectors, idf


def _compute_best_sim(
    entry: dict,
    mode: str,
    corpus_data: list,
    idf: dict,
    src_embs: dict[int, list[float]],
) -> float:
    """Return best similarity of *entry* against local corpus."""
    if not corpus_data:
        return 0.0

    if mode == "embedding":
        entry_vec = src_embs.get(entry.get("id"))
        if entry_vec:
            return _best_dense_sim(entry_vec, corpus_data)
        # Fall through to TF-IDF if no embedding stored for this entry

    text = f"{entry.get('title', '')} {entry.get('content', '')}"
    # Build on-the-fly TF-IDF index if corpus_data is tfidf vectors
    if mode == "tfidf" and corpus_data:
        return _best_tfidf_sim(text, corpus_data, idf)

    # No usable index
    return 0.0


def _run(args: argparse.Namespace) -> int:
    source_path = Path(args.from_db).expanduser().resolve()
    if not source_path.exists():
        print(f"Error: source DB not found: {source_path}", file=sys.stderr)
        return 1

    local_db_path = Path(os.environ.get("SK_DB_PATH", str(DB_PATH))).expanduser()
    if not local_db_path.exists() and not args.dry_run:
        print(f"Error: local DB not found: {local_db_path}", file=sys.stderr)
        print("Hint: run 'sk index build' or 'python3 build-session-index.py' first.", file=sys.stderr)
        return 1

    categories = [c.strip() for c in args.categories.split(",") if c.strip()] if args.categories else None
    tag_filter = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
    min_confidence: float = args.min_confidence
    sim_threshold: float = args.similarity
    limit: int = args.limit
    dry_run: bool = args.dry_run
    json_output: bool = args.json

    src_db = _open_source_db(source_path)
    try:
        source_entries = _load_source_entries(
            src_db,
            categories=categories,
            min_confidence=min_confidence,
            tag_filter=tag_filter,
            limit=limit,
        )
    finally:
        src_db.close()

    if not source_entries:
        _print_result(
            json_output,
            {
                "source": str(source_path),
                "source_total": 0,
                "candidates": 0,
                "accepted": 0,
                "skipped_dup": 0,
                "skipped_near_dup": 0,
                "skipped_low_sim": 0,
                "imported": 0,
                "entries": [],
            },
        )
        return 0

    # Open source DB again for embeddings
    src_db2 = _open_source_db(source_path)
    src_embs = _load_embeddings(src_db2)
    src_db2.close()

    # Open (or create a read-only stub) local DB
    local_exists = local_db_path.exists()
    if local_exists:
        local_db = _open_local_db(local_db_path)
    else:
        # In dry-run without a local DB: work in-memory
        local_db = sqlite3.connect(":memory:")
        local_db.row_factory = sqlite3.Row

    try:
        mode, corpus_data, idf = _build_similarity_index(local_db)
        local_texts = _load_local_texts(local_db) if local_exists else []
        if mode == "embedding" and not src_embs and local_texts:
            corpus_data, idf = _build_tfidf_index(local_texts)
            mode = "tfidf"
        local_count = len(local_texts)
        bypass_sim = local_count < 3  # not enough context for meaningful low-sim filtering

        extra_tag = _source_tag(source_path)
        now = datetime.now(timezone.utc).isoformat()

        stats = {
            "source": str(source_path),
            "source_total": len(source_entries),
            "candidates": 0,
            "accepted": 0,
            "skipped_dup": 0,
            "skipped_near_dup": 0,
            "skipped_low_sim": 0,
            "imported": 0,
        }
        entries_out: list[dict] = []

        for entry in source_entries:
            stats["candidates"] += 1
            category = entry.get("category", "discovery")
            title = entry.get("title", "")
            entry_id = entry.get("id", 0)

            # Dedup: already exists locally?
            if local_exists and _local_entry_exists(local_db, category, title):
                stats["skipped_dup"] += 1
                if dry_run:
                    entries_out.append(_dry_run_row(entry, 0.0, "skip", "duplicate"))
                continue

            best_sim = _compute_best_sim(entry, mode, corpus_data, idf, src_embs)

            if best_sim > NEAR_DUP_THRESHOLD:
                stats["skipped_near_dup"] += 1
                if dry_run:
                    entries_out.append(_dry_run_row(entry, best_sim, "skip", "near_dup"))
                continue
            if not bypass_sim and best_sim < sim_threshold:
                stats["skipped_low_sim"] += 1
                if dry_run:
                    entries_out.append(_dry_run_row(entry, best_sim, "skip", "low_sim"))
                continue

            stats["accepted"] += 1
            if dry_run:
                entries_out.append(_dry_run_row(entry, best_sim, "accept", "ok"))
            else:
                if local_exists and _has_table(local_db, "knowledge_entries"):
                    _insert_entry(local_db, entry, extra_tag)
                    stats["imported"] += 1
                    entries_out.append(_dry_run_row(entry, best_sim, "imported", "ok"))

        if not dry_run and local_exists and _has_table(local_db, "knowledge_entries"):
            local_db.commit()

        result = {**stats, "entries": entries_out}
        _print_result(json_output, result)

    finally:
        local_db.close()

    return 0


def _dry_run_row(entry: dict, sim: float, decision: str, reason: str) -> dict:
    return {
        "id": entry.get("id"),
        "category": entry.get("category"),
        "title": entry.get("title"),
        "confidence": entry.get("confidence"),
        "best_local_sim": round(sim, 4),
        "decision": decision,
        "reason": reason,
    }


def _print_result(json_output: bool, result: dict) -> None:
    if json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(f"Source:  {result['source']}")
    print(f"Entries in source: {result['source_total']}")
    print(f"Accepted:          {result['accepted']}")
    print(f"Imported:          {result['imported']}")
    print(f"Skipped (dup):     {result['skipped_dup']}")
    print(f"Skipped (near-dup):{result['skipped_near_dup']}")
    print(f"Skipped (low sim): {result['skipped_low_sim']}")

    entries = result.get("entries", [])
    if entries:
        print()
        header = f"{'ID':>6}  {'Category':<12}  {'Sim':>5}  {'Decision':<9}  {'Reason':<10}  Title"
        print(header)
        print("-" * len(header))
        for e in entries:
            print(
                f"{e['id'] or '':>6}  {e['category'] or '':.<12}  "
                f"{e['best_local_sim']:>5.3f}  {e['decision']:<9}  "
                f"{e['reason']:<10}  {(e['title'] or '')[:60]}"
            )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="knowledge-import",
        description="Import knowledge entries from another project's knowledge.db.",
    )
    p.add_argument(
        "--from",
        dest="from_db",
        required=True,
        metavar="PATH",
        help="Path to the source knowledge.db to import from.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print candidate entries with similarity scores; do not write to DB.",
    )
    p.add_argument(
        "--similarity",
        type=float,
        default=0.75,
        metavar="FLOAT",
        help="Minimum cosine similarity to any local entry (default: 0.75). "
        "Entries above 0.9 are treated as near-duplicates and skipped.",
    )
    p.add_argument(
        "--categories",
        metavar="LIST",
        help="Comma-separated list of categories to import (e.g. mistake,pattern).",
    )
    p.add_argument(
        "--tags",
        metavar="LIST",
        help="Only import source entries that have at least one of these tags.",
    )
    p.add_argument(
        "--min-confidence",
        type=float,
        default=0.0,
        dest="min_confidence",
        metavar="FLOAT",
        help="Skip source entries with confidence below this floor (default: 0.0).",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=50,
        metavar="N",
        help="Maximum number of source entries to consider (default: 50, 0 = no limit).",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON output.",
    )
    return p


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return _run(args)


if __name__ == "__main__":
    sys.exit(main())
