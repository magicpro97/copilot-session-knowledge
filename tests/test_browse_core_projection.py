#!/usr/bin/env python3
"""test_browse_core_projection.py — Unit tests for browse/core/projection.py."""

import json
import math
import os
import struct
import sys
import tempfile
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from browse.core.projection import (  # noqa: E402
    _dot,
    _norm,
    _normalize,
    _mat_vec,
    _mat_T_vec,
    _deflate,
    _decode_vector,
    pca_2d,
    _load_cache,
    _save_cache,
    CATEGORY_COLORS,
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


# ── Vector math helpers ───────────────────────────────────────────────────────

def test_dot_basic():
    result = _dot([1.0, 2.0, 3.0], [4.0, 5.0, 6.0])
    test("dot: basic", abs(result - 32.0) < 1e-9)


def test_dot_orthogonal():
    result = _dot([1.0, 0.0], [0.0, 1.0])
    test("dot: orthogonal → 0", abs(result) < 1e-9)


def test_dot_same_vector():
    v = [3.0, 4.0]
    result = _dot(v, v)
    test("dot: self-dot = squared norm", abs(result - 25.0) < 1e-9)


def test_norm_basic():
    result = _norm([3.0, 4.0])
    test("norm: 3-4-5 triangle", abs(result - 5.0) < 1e-9)


def test_norm_unit():
    result = _norm([1.0, 0.0, 0.0])
    test("norm: unit vector = 1", abs(result - 1.0) < 1e-9)


def test_norm_zero():
    result = _norm([0.0, 0.0, 0.0])
    test("norm: zero vector = 0", abs(result) < 1e-9)


def test_normalize_basic():
    v = _normalize([3.0, 4.0])
    n = math.sqrt(sum(x * x for x in v))
    test("normalize: result has unit norm", abs(n - 1.0) < 1e-9)


def test_normalize_zero_vector():
    """Zero vector should return itself (not crash)."""
    v = _normalize([0.0, 0.0, 0.0])
    test("normalize: zero vector no crash", isinstance(v, list))


def test_normalize_already_unit():
    v = _normalize([1.0, 0.0])
    test("normalize: unit vector unchanged", abs(v[0] - 1.0) < 1e-9 and abs(v[1]) < 1e-9)


def test_mat_vec_basic():
    X = [[1.0, 2.0], [3.0, 4.0]]
    v = [1.0, 1.0]
    result = _mat_vec(X, v)
    test("mat_vec: basic", abs(result[0] - 3.0) < 1e-9 and abs(result[1] - 7.0) < 1e-9)


def test_mat_T_vec_basic():
    X = [[1.0, 2.0], [3.0, 4.0]]
    w = [1.0, 1.0]
    result = _mat_T_vec(X, w)
    # X^T @ w = [1+3, 2+4] = [4, 6]
    test("mat_T_vec: basic", abs(result[0] - 4.0) < 1e-9 and abs(result[1] - 6.0) < 1e-9)


def test_mat_T_vec_empty():
    result = _mat_T_vec([], [])
    test("mat_T_vec: empty → empty", result == [])


def test_deflate_removes_component():
    """After deflating along e1, projections along e1 should be ~0."""
    X = [[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]]
    e = [1.0, 0.0]
    Xd = _deflate(X, e)
    test("deflate: component removed", abs(Xd[0][0]) < 1e-9)
    test("deflate: other dim unchanged", abs(Xd[0][1]) < 1e-9)


# ── _decode_vector ────────────────────────────────────────────────────────────

def test_decode_vector_basic():
    values = [1.0, 2.0, 3.0]
    blob = struct.pack("<3f", *values)
    result = _decode_vector(blob, 3)
    test("decode_vector: length 3", len(result) == 3)
    test("decode_vector: values approx", all(abs(result[i] - values[i]) < 1e-5 for i in range(3)))


def test_decode_vector_too_short():
    blob = struct.pack("<2f", 1.0, 2.0)
    result = _decode_vector(blob, 5)
    test("decode_vector: too short → empty", result == [])


def test_decode_vector_empty_blob():
    result = _decode_vector(b"", 3)
    test("decode_vector: empty blob → empty", result == [])


# ── pca_2d ────────────────────────────────────────────────────────────────────

def test_pca_2d_empty():
    xs, ys = pca_2d([])
    test("pca_2d: empty → empty", xs == [] and ys == [])


def test_pca_2d_1d_input():
    """Vectors with only 1 dimension should return xs from values, ys all zero."""
    vectors = [[1.0], [2.0], [3.0]]
    xs, ys = pca_2d(vectors)
    test("pca_2d: 1d xs len", len(xs) == 3)
    test("pca_2d: 1d ys all zero", all(abs(y) < 1e-9 for y in ys))


def test_pca_2d_returns_correct_length():
    import random as _r
    rng = _r.Random(42)
    n = 20
    vectors = [[rng.gauss(0, 1) for _ in range(8)] for _ in range(n)]
    xs, ys = pca_2d(vectors)
    test("pca_2d: xs length", len(xs) == n)
    test("pca_2d: ys length", len(ys) == n)


def test_pca_2d_all_same_vectors():
    """All identical vectors → all projected to same point."""
    vectors = [[1.0, 2.0, 3.0]] * 5
    xs, ys = pca_2d(vectors)
    test("pca_2d: same vectors same xs", len(set(xs)) <= 1)


def test_pca_2d_2d_input():
    """2D vectors should produce non-trivial projections."""
    vectors = [[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0], [0.0, -1.0]]
    xs, ys = pca_2d(vectors)
    test("pca_2d: 2d xs len", len(xs) == 4)
    test("pca_2d: 2d ys len", len(ys) == 4)


# ── Cache helpers ─────────────────────────────────────────────────────────────

def test_load_cache_missing_file():
    result = _load_cache(Path("/nonexistent/path/cache.json"))
    test("load_cache: missing → None", result is None)


def test_save_and_load_cache():
    test_dir = Path(__file__).parent.parent / "tests"
    cache_path = test_dir / "_test_projection_cache.json"
    try:
        data = {"count": 5, "points": [{"id": 1, "x": 0.1, "y": 0.2}]}
        _save_cache(data, cache_path)
        loaded = _load_cache(cache_path)
        test("cache_roundtrip: not None", loaded is not None)
        test("cache_roundtrip: count", loaded["count"] == 5)
        test("cache_roundtrip: points length", len(loaded["points"]) == 1)
    finally:
        try:
            cache_path.unlink(missing_ok=True)
        except Exception:
            pass


def test_load_cache_invalid_json():
    test_dir = Path(__file__).parent.parent / "tests"
    cache_path = test_dir / "_test_bad_cache.json"
    try:
        cache_path.write_text("not valid json", encoding="utf-8")
        result = _load_cache(cache_path)
        test("load_cache: invalid json → None", result is None)
    finally:
        try:
            cache_path.unlink(missing_ok=True)
        except Exception:
            pass


def test_load_cache_missing_required_keys():
    test_dir = Path(__file__).parent.parent / "tests"
    cache_path = test_dir / "_test_incomplete_cache.json"
    try:
        cache_path.write_text(json.dumps({"count": 5}), encoding="utf-8")
        result = _load_cache(cache_path)
        test("load_cache: missing keys → None", result is None)
    finally:
        try:
            cache_path.unlink(missing_ok=True)
        except Exception:
            pass


# ── CATEGORY_COLORS ───────────────────────────────────────────────────────────

def test_category_colors_dict():
    test("category_colors: is dict", isinstance(CATEGORY_COLORS, dict))
    test("category_colors: has mistake", "mistake" in CATEGORY_COLORS)
    test("category_colors: has pattern", "pattern" in CATEGORY_COLORS)
    test("category_colors: has decision", "decision" in CATEGORY_COLORS)
    for key, val in CATEGORY_COLORS.items():
        test(f"category_colors: {key} is hex", val.startswith("#") and len(val) == 7)


if __name__ == "__main__":
    test_dot_basic()
    test_dot_orthogonal()
    test_dot_same_vector()
    test_norm_basic()
    test_norm_unit()
    test_norm_zero()
    test_normalize_basic()
    test_normalize_zero_vector()
    test_normalize_already_unit()
    test_mat_vec_basic()
    test_mat_T_vec_basic()
    test_mat_T_vec_empty()
    test_deflate_removes_component()
    test_decode_vector_basic()
    test_decode_vector_too_short()
    test_decode_vector_empty_blob()
    test_pca_2d_empty()
    test_pca_2d_1d_input()
    test_pca_2d_returns_correct_length()
    test_pca_2d_all_same_vectors()
    test_pca_2d_2d_input()
    test_load_cache_missing_file()
    test_save_and_load_cache()
    test_load_cache_invalid_json()
    test_load_cache_missing_required_keys()
    test_category_colors_dict()

    print(f"\n{'='*50}")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    if _FAIL:
        sys.exit(1)
