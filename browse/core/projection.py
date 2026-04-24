"""browse/core/projection.py — PCA-to-2D projection for knowledge embeddings (pure stdlib)."""
import json
import math
import os
import random
import struct
import sys
import time
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

_CACHE_PATH = Path.home() / ".copilot" / "session-state" / "embeddings_2d_cache.json"
_MAX_RENDER = 2000   # cap points for rendering
_PCA_SAMPLE = 500    # rows used to compute eigenvectors (for speed)
_POWER_ITERS = 50    # power iteration count

CATEGORY_COLORS: dict = {
    "mistake":   "#ff6b6b",
    "pattern":   "#51cf66",
    "decision":  "#339af0",
    "discovery": "#cc5de8",
    "feature":   "#fcc419",
    "refactor":  "#ff922b",
    "tool":      "#20c997",
}


# ── Vector math helpers ──────────────────────────────────────────────────────

def _dot(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


def _norm(v) -> float:
    return math.sqrt(sum(x * x for x in v))


def _normalize(v) -> list:
    n = _norm(v)
    if n < 1e-12:
        return list(v)
    inv = 1.0 / n
    return [x * inv for x in v]


def _mat_vec(X, v) -> list:
    """X @ v  (list of rows × column vector)."""
    return [_dot(row, v) for row in X]


def _mat_T_vec(X, w) -> list:
    """X^T @ w  (uses rows of X as columns of X^T)."""
    if not X:
        return []
    n_dims = len(X[0])
    result = [0.0] * n_dims
    for i, row in enumerate(X):
        wi = w[i]
        if wi == 0.0:
            continue
        for j, xij in enumerate(row):
            result[j] += wi * xij
    return result


def _power_iter(X, n_iters: int, seed: int = 1) -> list:
    """Dominant eigenvector of X^T X via power iteration."""
    if not X or not X[0]:
        return []
    n_dims = len(X[0])
    rng = random.Random(seed)
    v = _normalize([rng.gauss(0, 1) for _ in range(n_dims)])
    for _ in range(n_iters):
        w = _mat_vec(X, v)       # X @ v   →  n_samples-dim
        u = _mat_T_vec(X, w)     # X^T @ w →  n_dims-dim
        v = _normalize(u)
    return v


def _deflate(X, e) -> list:
    """Remove component along unit vector e from each row of X."""
    return [[x - _dot(row, e) * ei for x, ei in zip(row, e)] for row in X]


def _decode_vector(blob: bytes, n_dims: int) -> list:
    """Decode float32 BLOB → list of floats."""
    n = len(blob) // 4
    if n < n_dims:
        return []
    return list(struct.unpack_from(f"<{n_dims}f", blob))


# ── PCA 2D ───────────────────────────────────────────────────────────────────

def pca_2d(vectors: list) -> tuple:
    """
    Project *vectors* (list of equal-length float lists) to 2D via PCA.
    Uses power iteration on a sample for speed.
    Returns (xs: list[float], ys: list[float]).
    """
    n = len(vectors)
    if n == 0:
        return [], []
    n_dims = len(vectors[0])
    if n_dims < 2:
        return [float(v[0]) if v else 0.0 for v in vectors], [0.0] * n

    # Centre
    mean = [sum(vectors[i][j] for i in range(n)) / n for j in range(n_dims)]
    centered = [[v[j] - mean[j] for j in range(n_dims)] for v in vectors]

    # Sample for eigenvector computation
    if n > _PCA_SAMPLE:
        idx = random.Random(99).sample(range(n), _PCA_SAMPLE)
        sample = [centered[i] for i in idx]
    else:
        sample = centered

    e1 = _power_iter(sample, _POWER_ITERS, seed=1)
    if not e1:
        return [0.0] * n, [0.0] * n

    sample_d = _deflate(sample, e1)
    e2 = _power_iter(sample_d, _POWER_ITERS, seed=2)
    if not e2:
        e2 = [0.0] * n_dims

    xs = [_dot(row, e1) for row in centered]
    ys = [_dot(row, e2) for row in centered]
    return xs, ys


# ── DB helpers ───────────────────────────────────────────────────────────────

def _count_db_embeddings(db) -> int:
    try:
        row = db.execute(
            "SELECT COUNT(*) FROM embeddings WHERE source_type = 'knowledge'"
        ).fetchone()
        return row[0] if row else 0
    except Exception:
        return 0


def _load_raw(db) -> list:
    """Load embeddings joined with knowledge_entries. Returns list of dicts."""
    try:
        rows = db.execute("""
            SELECT e.id, e.source_id, e.dimensions, e.vector,
                   ke.category, ke.title
            FROM embeddings e
            LEFT JOIN knowledge_entries ke
                   ON ke.id = e.source_id AND e.source_type = 'knowledge'
            WHERE e.source_type = 'knowledge'
              AND e.vector IS NOT NULL
        """).fetchall()
    except Exception:
        return []

    result = []
    for row in rows:
        eid, src_id, dims, blob, cat, title = row
        if not blob or not dims:
            continue
        vec = _decode_vector(bytes(blob), int(dims))
        if not vec:
            continue
        result.append({
            "id": eid,
            "source_id": src_id,
            "category": cat or "unknown",
            "title": (title or f"entry-{src_id}")[:200],
            "vec": vec,
        })
    return result


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _load_cache(cache_path: Path) -> dict | None:
    try:
        if cache_path.exists():
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "points" in data and "count" in data:
                return data
    except Exception:
        pass
    return None


def _save_cache(data: dict, cache_path: Path) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: tmp + os.replace prevents readers from seeing partial JSON
        tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(data, separators=(",", ":")), encoding="utf-8"
        )
        import os as _os
        _os.replace(str(tmp_path), str(cache_path))
    except Exception:
        pass


# ── Public API ────────────────────────────────────────────────────────────────

def get_projection(db, timeout: float = 30.0, cache_path: Path | None = None) -> dict:
    """
    Return ``{points, count, cached}``.

    Points is a list of ``{id, x, y, category, title}`` dicts.
    Reads from cache when possible; recomputes when count mismatch.
    Raises ``RuntimeError`` if computation exceeds *timeout* seconds.
    """
    if cache_path is None:
        cache_path = _CACHE_PATH

    db_count = _count_db_embeddings(db)

    cache = _load_cache(cache_path)
    if cache is not None and cache.get("count") == db_count:
        return {"points": cache["points"], "count": db_count, "cached": True}

    if db_count == 0:
        return {"points": [], "count": 0, "cached": False}

    t_start = time.monotonic()

    raw = _load_raw(db)
    if not raw:
        return {"points": [], "count": 0, "cached": False}

    # Sample for rendering cap
    if len(raw) > _MAX_RENDER:
        raw = random.Random(42).sample(raw, _MAX_RENDER)

    vectors = [r["vec"] for r in raw]
    xs, ys = pca_2d(vectors)

    if time.monotonic() - t_start > timeout:
        raise RuntimeError(f"PCA exceeded {timeout:.0f}s timeout")

    points = [
        {
            "id": r["id"],
            "x": round(xs[i], 6),
            "y": round(ys[i], 6),
            "category": r["category"],
            "title": r["title"],
        }
        for i, r in enumerate(raw)
    ]

    _save_cache({"count": db_count, "points": points}, cache_path)
    return {"points": points, "count": db_count, "cached": False}
