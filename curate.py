#!/usr/bin/env python3
"""sk curate — automated pipeline to flag stale/redundant knowledge entries."""

import os
import sys

if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8")

import argparse
import datetime
import json
import re
import sqlite3
import time
from pathlib import Path

SESSION_STATE = Path.home() / ".copilot" / "tools"
DB_PATH = Path(os.environ.get("SK_DB_PATH", str(SESSION_STATE / "knowledge.db"))).expanduser()
STALE_DAYS = 90
STALE_MIN_RECALL = 2
LOW_CONFIDENCE = 0.3


def _get_db(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        print(f"Error: DB not found at {db_path}", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _jaccard_similarity(a: str, b: str) -> float:
    """Simple word-level Jaccard similarity."""
    words_a = set(re.findall(r"[a-z0-9]+", (a or "").lower()))
    words_b = set(re.findall(r"[a-z0-9]+", (b or "").lower()))
    if not words_a and not words_b:
        return 1.0
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


def _cmd_scan(args):
    """Flag stale, duplicate, and low-confidence entries as pending_review."""
    db = _get_db(Path(args.db))
    now = time.time()
    cutoff = now - STALE_DAYS * 86400
    flagged = 0

    # 1. Stale entries: old + low recall
    cutoff_iso = datetime.datetime.utcfromtimestamp(cutoff).isoformat()
    stale = db.execute(
        "SELECT ke.id, ke.title FROM knowledge_entries ke "
        "LEFT JOIN entry_recall_stats ers ON ers.entry_id = ke.id "
        "WHERE (ke.last_seen IS NULL OR ke.last_seen < ?) "
        "AND (ers.recall_count IS NULL OR ers.recall_count < ?) "
        "AND (ke.curation_state IS NULL OR ke.curation_state = '') "
        "AND (ke.is_resolved IS NULL OR ke.is_resolved = 0)",
        (cutoff_iso, STALE_MIN_RECALL),
    ).fetchall()
    for row in stale:
        db.execute(
            "UPDATE knowledge_entries SET curation_state = 'pending_review' WHERE id = ?",
            (row[0],),
        )
        print(f"  [stale] #{row[0]} — {row[1][:60]}")
        flagged += 1

    # 2. Low confidence
    low_conf = db.execute(
        "SELECT id, title FROM knowledge_entries "
        "WHERE confidence < ? AND (curation_state IS NULL OR curation_state = '')",
        (LOW_CONFIDENCE,),
    ).fetchall()
    for row in low_conf:
        db.execute(
            "UPDATE knowledge_entries SET curation_state = 'pending_review' WHERE id = ?",
            (row[0],),
        )
        print(f"  [low-conf] #{row[0]} — {row[1][:60]}")
        flagged += 1

    # 3. Near-duplicates (Jaccard > 0.7 on title+content)
    all_entries = db.execute(
        "SELECT id, title, content, confidence FROM knowledge_entries "
        "WHERE curation_state IS NULL OR curation_state = '' LIMIT 500"
    ).fetchall()
    seen_dups: set = set()
    for i, (id_a, title_a, content_a, conf_a) in enumerate(all_entries):
        for id_b, title_b, content_b, conf_b in all_entries[i + 1 :]:
            text_a = f"{title_a} {content_a or ''}"
            text_b = f"{title_b} {content_b or ''}"
            if _jaccard_similarity(text_a, text_b) > 0.7:
                # Flag the lower-confidence entry
                if (conf_b or 1.0) < (conf_a or 1.0):
                    flag_id, flag_title, other_id = id_b, title_b, id_a
                else:
                    flag_id, flag_title, other_id = id_a, title_a, id_b
                if flag_id not in seen_dups:
                    seen_dups.add(flag_id)
                    db.execute(
                        "UPDATE knowledge_entries SET curation_state = 'pending_review' WHERE id = ?",
                        (flag_id,),
                    )
                    print(f"  [dup] #{flag_id} ~ #{other_id} — {flag_title[:50]}")
                    flagged += 1

    db.commit()
    db.close()
    print(f"\nFlagged {flagged} entries as pending_review.")


def _cmd_list(args):
    """List entries flagged for review."""
    db = _get_db(Path(args.db))
    rows = db.execute(
        "SELECT id, category, title, confidence, curation_state FROM knowledge_entries "
        "WHERE curation_state = 'pending_review' "
        "ORDER BY confidence ASC LIMIT ?",
        (args.limit,),
    ).fetchall()
    if args.json:
        print(
            json.dumps(
                [{"id": r[0], "category": r[1], "title": r[2], "confidence": r[3], "state": r[4]} for r in rows],
                indent=2,
            )
        )
    else:
        print(f"Pending review ({len(rows)} entries):")
        for r in rows:
            print(f"  #{r[0]} [{r[1]}] conf={r[3]:.2f} [{r[4]}] — {r[2][:60]}")
    db.close()


def _cmd_resolve(args):
    """Resolve a flagged entry: keep, archive, or merge."""
    db = _get_db(Path(args.db))
    action = args.action or "keep"
    if action == "keep":
        db.execute("UPDATE knowledge_entries SET curation_state = NULL WHERE id = ?", (args.entry_id,))
        print(f"  Kept #{args.entry_id}")
    elif action == "archive":
        db.execute(
            "UPDATE knowledge_entries SET curation_state = 'archived' WHERE id = ?",
            (args.entry_id,),
        )
        print(f"  Archived #{args.entry_id}")
    db.commit()
    db.close()


def _cmd_stats(args):
    """Show curation statistics."""
    db = _get_db(Path(args.db))
    total = db.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
    pending = db.execute("SELECT COUNT(*) FROM knowledge_entries WHERE curation_state = 'pending_review'").fetchone()[0]
    archived = db.execute("SELECT COUNT(*) FROM knowledge_entries WHERE curation_state = 'archived'").fetchone()[0]
    print(f"Knowledge base: {total} total, {pending} pending review, {archived} archived")
    db.close()


def main():
    parser = argparse.ArgumentParser(description="sk curate — knowledge entry curation pipeline")
    parser.add_argument("--db", default=str(DB_PATH))
    sub = parser.add_subparsers(dest="cmd")

    scan_p = sub.add_parser("scan", help="Flag stale/duplicate/low-quality entries")
    scan_p.set_defaults(func=_cmd_scan)

    list_p = sub.add_parser("list", help="List pending review entries")
    list_p.add_argument("--limit", type=int, default=50)
    list_p.add_argument("--json", action="store_true")
    list_p.set_defaults(func=_cmd_list)

    res_p = sub.add_parser("resolve", help="Resolve a flagged entry")
    res_p.add_argument("entry_id", type=int)
    res_p.add_argument("--action", choices=["keep", "archive"], default="keep")
    res_p.set_defaults(func=_cmd_resolve)

    stats_p = sub.add_parser("stats", help="Curation statistics")
    stats_p.set_defaults(func=_cmd_stats)

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return
    args.func(args)


if __name__ == "__main__":
    main()
