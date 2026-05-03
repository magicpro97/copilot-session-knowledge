#!/usr/bin/env python3
"""test_browse_core_similarity.py — Unit tests for browse/core/similarity.py."""

import json
import math
import os
import struct
import sys
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from browse.core.similarity import (  # noqa: E402
    _decode_vector,
    _fingerprint_rows,
    _unique_entry_ids,
    _is_better_neighbor,
    _build_top_neighbors_for_source,
    _compute_missing_neighbors,
    get_similarity,
    _EPS,
    _MAX_K,
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


def _make_blob(values):
    return struct.pack(f"<{len(values)}f", *values)


def _make_rows(specs):
    """Build synthetic rows from list of (entry_id, vec) tuples."""
    rows = []
    for entry_id, vec in specs:
        blob = _make_blob(vec)
        norm = math.sqrt(sum(v * v for v in vec))
        rows.append({
            "entry_id": entry_id,
            "title": f"entry-{entry_id}",
            "category": "test",
            "vec": vec,
            "norm": norm,
            "blob": blob,
            "dims": len(vec),
        })
    return rows


# ── _decode_vector ────────────────────────────────────────────────────────────

def test_decode_basic():
    values = [0.5, 1.0, 1.5]
    blob = _make_blob(values)
    result = _decode_vector(blob, 3)
    test("decode: len 3", len(result) == 3)
    test("decode: values", all(abs(result[i] - values[i]) < 1e-5 for i in range(3)))


def test_decode_too_short():
    blob = _make_blob([1.0])
    result = _decode_vector(blob, 3)
    test("decode: too short → empty", result == [])


# ── _unique_entry_ids ─────────────────────────────────────────────────────────

def test_unique_entry_ids_no_dupes():
    result = _unique_entry_ids([1, 2, 3])
    test("unique: no dupes unchanged", result == [1, 2, 3])


def test_unique_entry_ids_with_dupes():
    result = _unique_entry_ids([1, 2, 1, 3, 2])
    test("unique: dupes removed", result == [1, 2, 3])


def test_unique_entry_ids_empty():
    result = _unique_entry_ids([])
    test("unique: empty → empty", result == [])


def test_unique_entry_ids_preserves_order():
    result = _unique_entry_ids([5, 3, 1, 3, 5])
    test("unique: order preserved", result == [5, 3, 1])


# ── _is_better_neighbor ───────────────────────────────────────────────────────

def test_is_better_neighbor_higher_score():
    worst = (0.5, -10, "t1", "cat1")  # (score, inv_id, title, category)
    result = _is_better_neighbor(0.8, 5, worst)
    test("better_neighbor: higher score wins", result is True)


def test_is_better_neighbor_lower_score():
    worst = (0.8, -10, "t1", "cat1")
    result = _is_better_neighbor(0.3, 5, worst)
    test("better_neighbor: lower score loses", result is False)


def test_is_better_neighbor_tie_smaller_id():
    """Tie-breaking: smaller entry_id wins (more stable)."""
    worst = (0.5, -10, "t1", "cat1")  # entry_id = 10
    result = _is_better_neighbor(0.5, 5, worst)  # entry_id 5 < 10
    test("better_neighbor: tie smaller id wins", result is True)


def test_is_better_neighbor_tie_larger_id():
    worst = (0.5, -5, "t1", "cat1")  # entry_id = 5
    result = _is_better_neighbor(0.5, 10, worst)  # entry_id 10 > 5
    test("better_neighbor: tie larger id loses", result is False)


# ── _fingerprint_rows ─────────────────────────────────────────────────────────

def test_fingerprint_deterministic():
    rows = _make_rows([(1, [1.0, 2.0]), (2, [3.0, 4.0])])
    fp1 = _fingerprint_rows(rows)
    fp2 = _fingerprint_rows(rows)
    test("fingerprint: deterministic", fp1 == fp2)


def test_fingerprint_different_rows():
    rows1 = _make_rows([(1, [1.0, 2.0])])
    rows2 = _make_rows([(1, [3.0, 4.0])])
    fp1 = _fingerprint_rows(rows1)
    fp2 = _fingerprint_rows(rows2)
    test("fingerprint: different rows differ", fp1 != fp2)


def test_fingerprint_empty():
    fp = _fingerprint_rows([])
    test("fingerprint: empty is string", isinstance(fp, str))
    test("fingerprint: empty has length", len(fp) == 64)  # sha256 hex = 64 chars


# ── _build_top_neighbors_for_source ──────────────────────────────────────────

def test_build_top_neighbors_basic():
    rows = _make_rows([
        (1, [1.0, 0.0]),
        (2, [1.0, 0.0]),  # identical to src → cos=1.0
        (3, [0.0, 1.0]),  # orthogonal → cos=0.0
        (4, [-1.0, 0.0]),  # opposite → cos=-1.0
    ])
    src = rows[0]
    neighbors, pairs = _build_top_neighbors_for_source(src, rows, max_neighbors=3)
    test("build_neighbors: returns list", isinstance(neighbors, list))
    test("build_neighbors: pair count", pairs > 0)
    test("build_neighbors: max k respected", len(neighbors) <= 3)
    # Most similar should come first
    if neighbors:
        test("build_neighbors: sorted by score desc", all(
            neighbors[i]["score"] >= neighbors[i + 1]["score"]
            for i in range(len(neighbors) - 1)
        ))


def test_build_top_neighbors_excludes_self():
    rows = _make_rows([(1, [1.0, 0.0]), (2, [0.5, 0.5])])
    src = rows[0]
    neighbors, _ = _build_top_neighbors_for_source(src, rows, max_neighbors=5)
    ids = [n["id"] for n in neighbors]
    test("build_neighbors: no self", 1 not in ids)


def test_build_top_neighbors_zero_norm():
    """Zero-norm vectors should be skipped gracefully."""
    rows = _make_rows([
        (1, [1.0, 0.0]),
        (2, [0.0, 0.0]),  # zero norm
    ])
    src = rows[0]
    neighbors, _ = _build_top_neighbors_for_source(src, rows, max_neighbors=5)
    test("build_neighbors: zero norm skipped", all(n["id"] != 2 for n in neighbors))


# ── _compute_missing_neighbors ────────────────────────────────────────────────

def test_compute_missing_empty_source_ids():
    rows = _make_rows([(1, [1.0, 0.0]), (2, [0.0, 1.0])])
    neighbors, skipped, pairs = _compute_missing_neighbors(rows, [], 5, 10000)
    test("compute_missing: empty sources → empty", neighbors == {})
    test("compute_missing: no skipped", skipped == [])


def test_compute_missing_valid_source():
    rows = _make_rows([(1, [1.0, 0.0]), (2, [0.0, 1.0]), (3, [1.0, 1.0])])
    neighbors, skipped, pairs = _compute_missing_neighbors(rows, [1], 2, 100000)
    test("compute_missing: entry 1 computed", "1" in neighbors)
    test("compute_missing: neighbors list", isinstance(neighbors["1"], list))


def test_compute_missing_invalid_source_id():
    rows = _make_rows([(1, [1.0, 0.0])])
    neighbors, skipped, pairs = _compute_missing_neighbors(rows, [999], 5, 100000)
    test("compute_missing: invalid id → empty", neighbors == {})


# ── get_similarity (with mock DB) ─────────────────────────────────────────────

class _MockDB:
    """In-memory mock DB that returns pre-canned embedding rows."""

    def __init__(self, rows=None):
        self._rows = rows or []

    def execute(self, sql):
        class _Result:
            def __init__(self, rows):
                self._rows = rows

            def fetchall(self_inner):
                return self_inner._rows

        return _Result(self._rows)


def test_get_similarity_empty_db():
    db = _MockDB(rows=[])
    result = get_similarity(db, [1, 2], k=3, cache_path=Path("/nonexistent/x.json"))
    test("get_sim: empty db results list", isinstance(result["results"], list))
    test("get_sim: empty db meta", "method" in result["meta"])
    test("get_sim: empty db embedding_count 0", result["meta"]["embedding_count"] == 0)
    # Empty neighbors for each requested id
    for item in result["results"]:
        test("get_sim: empty neighbors", item["neighbors"] == [])


def test_get_similarity_k_clamped():
    db = _MockDB(rows=[])
    result = get_similarity(db, [1], k=999, cache_path=Path("/nonexistent/x.json"))
    test("get_sim: k clamped to max", result["meta"]["k"] <= _MAX_K)


def test_get_similarity_k_min():
    db = _MockDB(rows=[])
    result = get_similarity(db, [1], k=0, cache_path=Path("/nonexistent/x.json"))
    test("get_sim: k min 1", result["meta"]["k"] >= 1)


if __name__ == "__main__":
    test_decode_basic()
    test_decode_too_short()
    test_unique_entry_ids_no_dupes()
    test_unique_entry_ids_with_dupes()
    test_unique_entry_ids_empty()
    test_unique_entry_ids_preserves_order()
    test_is_better_neighbor_higher_score()
    test_is_better_neighbor_lower_score()
    test_is_better_neighbor_tie_smaller_id()
    test_is_better_neighbor_tie_larger_id()
    test_fingerprint_deterministic()
    test_fingerprint_different_rows()
    test_fingerprint_empty()
    test_build_top_neighbors_basic()
    test_build_top_neighbors_excludes_self()
    test_build_top_neighbors_zero_norm()
    test_compute_missing_empty_source_ids()
    test_compute_missing_valid_source()
    test_compute_missing_invalid_source_id()
    test_get_similarity_empty_db()
    test_get_similarity_k_clamped()
    test_get_similarity_k_min()

    print(f"\n{'='*50}")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    if _FAIL:
        sys.exit(1)
