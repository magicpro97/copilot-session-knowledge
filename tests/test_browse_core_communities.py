#!/usr/bin/env python3
"""test_browse_core_communities.py — Unit tests for browse/core/communities.py."""

import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from browse.core.communities import (  # noqa: E402
    _top_counts,
    _top_relation_counts,
    get_communities,
)

_PASS = 0
_FAIL = 0


def test(name: str, expr: bool) -> None:
    global _PASS, _FAIL
    if expr:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}")


def _make_db(entries=None, relations=None):
    """Build an in-memory SQLite DB with knowledge tables."""
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE knowledge_entries (
            id INTEGER PRIMARY KEY,
            title TEXT,
            category TEXT,
            wing TEXT
        );
        CREATE TABLE knowledge_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER,
            target_id INTEGER,
            relation_type TEXT
        );
    """)
    if entries:
        db.executemany(
            "INSERT INTO knowledge_entries (id, title, category, wing) VALUES (?, ?, ?, ?)",
            entries,
        )
    if relations:
        db.executemany(
            "INSERT INTO knowledge_relations (source_id, target_id, relation_type) VALUES (?, ?, ?)",
            relations,
        )
    db.commit()
    return db


# ── _top_counts ───────────────────────────────────────────────────────────────

def test_top_counts_basic():
    counter = Counter({"a": 5, "b": 3, "c": 1})
    result = _top_counts(counter, limit=2)
    test("top_counts: length 2", len(result) == 2)
    test("top_counts: first is highest", result[0]["name"] == "a")
    test("top_counts: count present", result[0]["count"] == 5)


def test_top_counts_empty():
    result = _top_counts(Counter(), limit=3)
    test("top_counts: empty → empty list", result == [])


def test_top_counts_tie_breaks_alphabetically():
    counter = Counter({"b": 2, "a": 2, "c": 1})
    result = _top_counts(counter, limit=2)
    test("top_counts: tie-break alpha", result[0]["name"] == "a")


def test_top_counts_limit_exceeds_count():
    counter = Counter({"x": 3})
    result = _top_counts(counter, limit=10)
    test("top_counts: limit > items returns all", len(result) == 1)


# ── _top_relation_counts ──────────────────────────────────────────────────────

def test_top_relation_counts_basic():
    counter = Counter({"causes": 4, "related_to": 2})
    result = _top_relation_counts(counter, limit=2)
    test("top_relation: length 2", len(result) == 2)
    test("top_relation: type field present", "type" in result[0])
    test("top_relation: count field present", "count" in result[0])
    test("top_relation: first is highest", result[0]["type"] == "causes")


def test_top_relation_counts_empty():
    result = _top_relation_counts(Counter(), limit=3)
    test("top_relation: empty → empty list", result == [])


# ── get_communities ───────────────────────────────────────────────────────────

def test_get_communities_empty_db():
    db = _make_db()
    result = get_communities(db)
    test("communities: empty db → communities key", "communities" in result)
    test("communities: empty db → empty list", result["communities"] == [])


def test_get_communities_no_relations():
    """Entries with no edges → no communities (adjacency empty)."""
    entries = [(1, "E1", "mistake", "core"), (2, "E2", "pattern", "api")]
    db = _make_db(entries=entries)
    result = get_communities(db)
    test("communities: no edges → empty", result["communities"] == [])


def test_get_communities_single_component():
    entries = [(1, "E1", "mistake", "core"), (2, "E2", "pattern", "core"), (3, "E3", "decision", "api")]
    relations = [(1, 2, "causes"), (2, 3, "related_to")]
    db = _make_db(entries=entries, relations=relations)
    result = get_communities(db)
    test("communities: single component found", len(result["communities"]) == 1)
    c = result["communities"][0]
    test("communities: entry_count", c["entry_count"] == 3)
    test("communities: top_categories present", "top_categories" in c)
    test("communities: wings present", "wings" in c)
    test("communities: rep entries present", "representative_entries" in c)


def test_get_communities_two_components():
    entries = [
        (1, "E1", "mistake", "core"),
        (2, "E2", "pattern", "core"),
        (3, "E3", "decision", "api"),
        (4, "E4", "tool", "api"),
    ]
    relations = [(1, 2, "causes"), (3, 4, "related_to")]
    db = _make_db(entries=entries, relations=relations)
    result = get_communities(db)
    test("communities: two components", len(result["communities"]) == 2)


def test_get_communities_self_loop_ignored():
    """Self-loops should not form a community."""
    entries = [(1, "E1", "mistake", "core"), (2, "E2", "pattern", "api")]
    relations = [(1, 1, "self")]  # self-loop
    db = _make_db(entries=entries, relations=relations)
    result = get_communities(db)
    test("communities: self-loop ignored → empty", result["communities"] == [])


def test_get_communities_min_entry_count_filter():
    """Components smaller than min_entry_count should be excluded."""
    entries = [(1, "E1", "mistake", "core"), (2, "E2", "pattern", "core")]
    relations = [(1, 2, "causes")]
    db = _make_db(entries=entries, relations=relations)
    result = get_communities(db, min_entry_count=3)
    test("communities: min_entry_count filters small components", result["communities"] == [])


def test_get_communities_id_format():
    entries = [(10, "E10", "mistake", "core"), (20, "E20", "pattern", "core"), (30, "E30", "decision", "api")]
    relations = [(10, 20, "causes"), (20, 30, "related_to")]
    db = _make_db(entries=entries, relations=relations)
    result = get_communities(db)
    if result["communities"]:
        c = result["communities"][0]
        test("communities: id format c-N", c["id"].startswith("c-"))


def test_get_communities_sorted_by_size():
    """Larger communities should come first."""
    entries = [
        (1, "E1", "mistake", "core"),
        (2, "E2", "pattern", "core"),
        (3, "E3", "decision", "api"),
        (4, "E4", "tool", "api"),
        (5, "E5", "tool", "api"),
    ]
    relations = [(1, 2, "causes"), (3, 4, "related_to"), (4, 5, "related_to")]
    db = _make_db(entries=entries, relations=relations)
    result = get_communities(db)
    if len(result["communities"]) >= 2:
        test("communities: sorted desc by size", result["communities"][0]["entry_count"] >= result["communities"][1]["entry_count"])


def test_get_communities_representative_entries_limit():
    """Representative entries should be at most 3."""
    entries = [(i, f"E{i}", "mistake", "core") for i in range(1, 8)]
    relations = [(i, i + 1, "causes") for i in range(1, 7)]
    db = _make_db(entries=entries, relations=relations)
    result = get_communities(db)
    if result["communities"]:
        rep = result["communities"][0]["representative_entries"]
        test("communities: max 3 rep entries", len(rep) <= 3)


def test_get_communities_db_error_graceful():
    """DB with wrong schema should return empty communities."""
    db = sqlite3.connect(":memory:")
    result = get_communities(db)
    test("communities: bad schema → graceful", result == {"communities": []})


if __name__ == "__main__":
    test_top_counts_basic()
    test_top_counts_empty()
    test_top_counts_tie_breaks_alphabetically()
    test_top_counts_limit_exceeds_count()
    test_top_relation_counts_basic()
    test_top_relation_counts_empty()
    test_get_communities_empty_db()
    test_get_communities_no_relations()
    test_get_communities_single_component()
    test_get_communities_two_components()
    test_get_communities_self_loop_ignored()
    test_get_communities_min_entry_count_filter()
    test_get_communities_id_format()
    test_get_communities_sorted_by_size()
    test_get_communities_representative_entries_limit()
    test_get_communities_db_error_graceful()

    print(f"\n{'='*50}")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    if _FAIL:
        sys.exit(1)
