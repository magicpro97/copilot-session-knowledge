#!/usr/bin/env python3
"""
tag-entries.py — Batch concept-tag extraction for knowledge entries.

Scans knowledge_entries rows that have no auto-generated concept tags and
writes extracted tags to the entry_concept_tags table.  All tags are stored
with source='auto' and carry local_only sync scope (never replicated).

Usage:
    python tag-entries.py                # Tag all untagged entries
    python tag-entries.py --all          # Re-tag every entry (replace stale tags)
    python tag-entries.py --dry-run      # Preview without writing
    python tag-entries.py --limit N      # Process at most N entries
    python tag-entries.py --stats        # Show current concept-tag coverage stats
"""

import os
import re
import sqlite3
import sys
import time
from pathlib import Path

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SESSION_STATE = Path.home() / ".copilot" / "session-state"
DB_PATH = SESSION_STATE / "knowledge.db"

# ---------------------------------------------------------------------------
# Concept tag extraction (pure stdlib, no ML imports)
# ---------------------------------------------------------------------------

_CONCEPT_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "can",
        "it",
        "this",
        "that",
        "these",
        "those",
        "i",
        "we",
        "you",
        "he",
        "she",
        "they",
        "not",
        "no",
        "so",
        "if",
        "then",
        "when",
        "where",
        "what",
        "which",
        "who",
        "how",
        "all",
        "any",
        "each",
        "more",
        "most",
        "also",
        "just",
        "up",
        "out",
        "as",
        "into",
        "than",
        "their",
        "its",
        "our",
        "my",
        "your",
        "his",
        "her",
        "them",
        "us",
        "me",
        "after",
        "before",
        "during",
        "while",
        "since",
        "until",
        "too",
        "very",
        "about",
        "above",
        "below",
        "between",
        "through",
        "use",
        "used",
        "using",
        "run",
        "running",
        "make",
        "new",
        "only",
        "now",
        "time",
        "way",
        "need",
        "needs",
        "see",
        "get",
        "set",
        "add",
        "put",
        "let",
        "say",
        "one",
        "two",
        "per",
        "via",
        "etc",
        "yet",
        "got",
    }
)


def extract_concept_tags(text: str, top_k: int = 5) -> list[str]:
    """Extract top_k concept tags from text using pure-stdlib term frequency.

    Distinct from existing tag parsing that reads explicit user-supplied tags.
    Pure stdlib: no numpy/sklearn/ML imports.

    Args:
        text: Combined title and content text to analyze.
        top_k: Maximum number of concept tags to return.

    Returns:
        List of up to top_k lowercase concept tag strings, sorted by frequency desc.
    """
    if not text:
        return []
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", text.lower())
    freq: dict[str, int] = {}
    for tok in tokens:
        if tok not in _CONCEPT_STOPWORDS:
            freq[tok] = freq.get(tok, 0) + 1
    ranked = sorted(freq.items(), key=lambda x: (-x[1], x[0]))
    return [tag for tag, _ in ranked[:top_k]]


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def get_db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        print("Error: Knowledge DB not found. Run build-session-index.py first.", file=sys.stderr)
        sys.exit(1)
    db = sqlite3.connect(str(DB_PATH), timeout=30.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=30000")
    return db


def _ensure_concept_tags_table(db: sqlite3.Connection) -> bool:
    """Create entry_concept_tags if absent. Returns True when table is available."""
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS entry_concept_tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id INTEGER NOT NULL,
                tag TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'auto',
                tagged_at TEXT DEFAULT (datetime('now')),
                UNIQUE(entry_id, tag)
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_ect_entry ON entry_concept_tags(entry_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_ect_tag ON entry_concept_tags(tag)")
        db.commit()
        return True
    except sqlite3.OperationalError as e:
        print(f"  [warn] Could not create entry_concept_tags: {e}", file=sys.stderr)
        return False


def _seed_sync_policy(db: sqlite3.Connection):
    """Seed local_only sync policy for entry_concept_tags (fail-open)."""
    try:
        db.execute("""
            INSERT INTO sync_table_policies (table_name, sync_scope, stable_id_column)
            VALUES ('entry_concept_tags', 'local_only', '')
            ON CONFLICT(table_name) DO NOTHING
        """)
        db.commit()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Core batch tagging logic
# ---------------------------------------------------------------------------


def run_batch_tag(
    retag_all: bool = False,
    dry_run: bool = False,
    limit: int = 0,
    quiet: bool = False,
) -> dict:
    """Run batch concept tagging.

    Args:
        retag_all: If True, replace existing auto tags for all entries.
                   If False (default), only process entries with no auto tags.
        dry_run: Preview only; do not write to DB.
        limit: Max entries to process (0 = no limit).
        quiet: Suppress per-entry output.

    Returns:
        Stats dict: processed, tagged, skipped, errors.
    """
    db = get_db()
    try:
        if not _ensure_concept_tags_table(db):
            return {"processed": 0, "tagged": 0, "skipped": 0, "errors": 0, "available": False}

        _seed_sync_policy(db)

        if retag_all:
            # Process every entry
            if limit > 0:
                query = "SELECT id, title, content FROM knowledge_entries ORDER BY id LIMIT ?"
                rows = db.execute(query, (limit,)).fetchall()
            else:
                query = "SELECT id, title, content FROM knowledge_entries ORDER BY id"
                rows = db.execute(query).fetchall()
        else:
            # Only entries without any auto-generated concept tags
            if limit > 0:
                query = """
                    SELECT ke.id, ke.title, ke.content
                    FROM knowledge_entries ke
                    WHERE NOT EXISTS (
                        SELECT 1 FROM entry_concept_tags ect
                        WHERE ect.entry_id = ke.id AND ect.source = 'auto'
                    )
                    ORDER BY ke.id
                    LIMIT ?
                """
                rows = db.execute(query, (limit,)).fetchall()
            else:
                query = """
                    SELECT ke.id, ke.title, ke.content
                    FROM knowledge_entries ke
                    WHERE NOT EXISTS (
                        SELECT 1 FROM entry_concept_tags ect
                        WHERE ect.entry_id = ke.id AND ect.source = 'auto'
                    )
                    ORDER BY ke.id
                """
                rows = db.execute(query).fetchall()

        stats = {"processed": 0, "tagged": 0, "skipped": 0, "errors": 0, "available": True}
        now = time.strftime("%Y-%m-%dT%H:%M:%S")

        for row in rows:
            entry_id = row["id"]
            title = row["title"] or ""
            content = row["content"] or ""
            stats["processed"] += 1

            try:
                tags = extract_concept_tags(f"{title} {content}", top_k=5)
                if not tags:
                    stats["skipped"] += 1
                    continue

                if not dry_run:
                    # Atomic delete-then-insert via SAVEPOINT so a failed insert
                    # cannot commit the deletion and silently erase existing tags.
                    db.execute("SAVEPOINT _batch_tag")
                    try:
                        db.execute(
                            "DELETE FROM entry_concept_tags WHERE entry_id = ? AND source = 'auto'",
                            (entry_id,),
                        )
                        db.executemany(
                            """
                            INSERT INTO entry_concept_tags (entry_id, tag, source, tagged_at)
                            VALUES (?, ?, 'auto', ?)
                            ON CONFLICT(entry_id, tag) DO UPDATE SET tagged_at = excluded.tagged_at
                            """,
                            [(entry_id, tag, now) for tag in tags],
                        )
                        db.execute("RELEASE _batch_tag")
                    except Exception:
                        db.execute("ROLLBACK TO _batch_tag")
                        db.execute("RELEASE _batch_tag")
                        raise

                stats["tagged"] += 1
                if not quiet:
                    mode = "[dry-run] " if dry_run else ""
                    print(f"  {mode}#{entry_id}: {', '.join(tags)}")
            except Exception as e:
                stats["errors"] += 1
                print(f"  [error] entry #{entry_id}: {e}", file=sys.stderr)

        if not dry_run:
            db.commit()
        return stats
    finally:
        db.close()


def run_stats() -> dict:
    """Return concept tag coverage statistics."""
    db = get_db()
    try:
        total = db.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
        has_ect = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='entry_concept_tags'").fetchone()
        if not has_ect:
            return {"total_entries": total, "tagged_entries": 0, "coverage_pct": 0.0, "available": False}

        tagged = db.execute("SELECT COUNT(DISTINCT entry_id) FROM entry_concept_tags WHERE source = 'auto'").fetchone()[
            0
        ]
        coverage_pct = round((tagged / total) * 100, 1) if total > 0 else 0.0
        total_tags = db.execute("SELECT COUNT(*) FROM entry_concept_tags WHERE source = 'auto'").fetchone()[0]
        top_tags = db.execute("""
            SELECT tag, COUNT(*) as freq
            FROM entry_concept_tags
            WHERE source = 'auto'
            GROUP BY tag
            ORDER BY freq DESC, tag ASC
            LIMIT 20
        """).fetchall()
        return {
            "total_entries": total,
            "tagged_entries": tagged,
            "coverage_pct": coverage_pct,
            "total_auto_tags": total_tags,
            "top_tags": [{"tag": r["tag"], "freq": r["freq"]} for r in top_tags],
            "available": True,
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Batch concept-tag extraction for knowledge entries.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--all", dest="retag_all", action="store_true", help="Re-tag all entries, replacing stale auto tags"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to DB")
    parser.add_argument("--limit", type=int, default=0, help="Process at most N entries (0 = no limit)")
    parser.add_argument("--stats", action="store_true", help="Show concept tag coverage statistics")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-entry output")

    args = parser.parse_args(argv)

    if args.stats:
        s = run_stats()
        if not s["available"]:
            print("  entry_concept_tags table not found — run without --stats to create it.")
            return 0
        print(f"  Total entries:      {s['total_entries']:,}")
        print(f"  Tagged entries:     {s['tagged_entries']:,}")
        print(f"  Coverage:           {s['coverage_pct']:.1f}%")
        print(f"  Total auto tags:    {s['total_auto_tags']:,}")
        if s.get("top_tags"):
            print("\n  Top concept tags:")
            for t in s["top_tags"][:10]:
                print(f"    {t['freq']:5d}x  {t['tag']}")
        return 0

    mode = "re-tagging all" if args.retag_all else "tagging untagged"
    if args.dry_run:
        mode = f"[dry-run] {mode}"
    limit_note = f" (limit {args.limit})" if args.limit > 0 else ""
    print(f"Concept tag batch — {mode} entries{limit_note}...")

    stats = run_batch_tag(
        retag_all=args.retag_all,
        dry_run=args.dry_run,
        limit=args.limit,
        quiet=args.quiet,
    )

    if not stats.get("available"):
        print("  Failed to initialize entry_concept_tags table.", file=sys.stderr)
        return 1

    print(
        f"\nDone — processed={stats['processed']}  tagged={stats['tagged']}  "
        f"skipped={stats['skipped']}  errors={stats['errors']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
