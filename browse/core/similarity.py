"""browse/core/similarity.py — cosine kNN over knowledge embeddings (pure stdlib)."""
import hashlib
import heapq
import json
import math
import os
import struct
import sys
import time
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

_CACHE_PATH = Path.home() / ".copilot" / "session-state" / "embeddings_similarity_cache.json"
_EPS = 1e-12
_MAX_K = 50
_MAX_REQUESTED_ENTRY_IDS = 200
_MAX_COMPUTE_PAIRS = 250_000
_CACHE_NEIGHBORS = 50


def _decode_vector(blob: bytes, n_dims: int) -> list[float]:
    n = len(blob) // 4
    if n < n_dims:
        return []
    return list(struct.unpack_from(f"<{n_dims}f", blob))


def _load_cache(cache_path: Path) -> dict | None:
    try:
        if cache_path.exists():
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "fingerprint" in data and "neighbors" in data:
                return data
    except Exception:
        pass
    return None


def _save_cache(data: dict, cache_path: Path) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        import os as _os

        _os.replace(str(tmp_path), str(cache_path))
    except Exception:
        pass


def _load_rows(db) -> list[dict]:
    try:
        rows = db.execute(
            """
            SELECT e.id, e.source_id, e.dimensions, e.vector, ke.title, ke.category
            FROM embeddings e
            LEFT JOIN knowledge_entries ke ON ke.id = e.source_id
            WHERE e.source_type = 'knowledge'
              AND e.vector IS NOT NULL
            ORDER BY e.source_id ASC, e.id DESC
            """
        ).fetchall()
    except Exception:
        return []

    deduped: list[dict] = []
    seen_source_ids: set[int] = set()
    for row in rows:
        source_id = int(row[1])
        if source_id in seen_source_ids:
            continue
        seen_source_ids.add(source_id)
        dims = int(row[2] or 0)
        blob = bytes(row[3] or b"")
        if dims <= 0 or not blob:
            continue
        vec = _decode_vector(blob, dims)
        if not vec:
            continue
        norm = math.sqrt(sum(v * v for v in vec))
        deduped.append(
            {
                "entry_id": source_id,
                "title": (row[4] or f"entry-{source_id}")[:200],
                "category": row[5] or "unknown",
                "vec": vec,
                "norm": norm,
                "blob": blob,
                "dims": dims,
            }
        )
    return deduped


def _fingerprint_rows(rows: list[dict]) -> str:
    h = hashlib.sha256()
    for row in rows:
        h.update(str(row["entry_id"]).encode("utf-8"))
        h.update(b"|")
        h.update(str(row["dims"]).encode("utf-8"))
        h.update(b"|")
        h.update(row["blob"])
        h.update(b"|")
        h.update(str(row["title"]).encode("utf-8", errors="replace"))
        h.update(b"|")
        h.update(str(row["category"]).encode("utf-8", errors="replace"))
        h.update(b"\n")
    return h.hexdigest()


def _is_better_neighbor(score: float, entry_id: int, worst: tuple[float, int, str, str]) -> bool:
    worst_score, worst_inv_id, _, _ = worst
    if score > worst_score:
        return True
    if abs(score - worst_score) <= _EPS and entry_id < -worst_inv_id:
        return True
    return False


def _build_top_neighbors_for_source(
    src: dict, rows: list[dict], max_neighbors: int
) -> tuple[list[dict], int]:
    src_norm = src["norm"]
    heap: list[tuple[float, int, str, str]] = []
    pairs = 0
    for dst in rows:
        if dst["entry_id"] == src["entry_id"]:
            continue
        pairs += 1
        denom = src_norm * dst["norm"]
        if denom <= _EPS:
            continue
        dot = sum(a * b for a, b in zip(src["vec"], dst["vec"]))
        score = dot / denom
        candidate = (score, -dst["entry_id"], dst["title"], dst["category"])
        if len(heap) < max_neighbors:
            heapq.heappush(heap, candidate)
            continue
        if _is_better_neighbor(score, dst["entry_id"], heap[0]):
            heapq.heapreplace(heap, candidate)
    ordered = sorted(heap, key=lambda item: (-item[0], -item[1]))
    return [
        {
            "id": -inv_id,
            "title": title,
            "category": category,
            "score": round(score, 6),
        }
        for score, inv_id, title, category in ordered
    ], pairs


def _unique_entry_ids(entry_ids: list[int]) -> list[int]:
    seen: set[int] = set()
    unique: list[int] = []
    for eid in entry_ids:
        if eid in seen:
            continue
        seen.add(eid)
        unique.append(eid)
    return unique


def _compute_missing_neighbors(
    rows: list[dict], source_entry_ids: list[int], max_neighbors: int, max_pairs: int
) -> tuple[dict[str, list[dict]], list[int], int]:
    rows_by_entry_id = {int(row["entry_id"]): row for row in rows}
    valid_sources = [eid for eid in source_entry_ids if eid in rows_by_entry_id]
    if not valid_sources:
        return {}, [], 0
    pairs_per_source = max(1, len(rows) - 1)
    allowed_sources = min(len(valid_sources), max(1, max_pairs // pairs_per_source))
    computed_ids = valid_sources[:allowed_sources]
    skipped_ids = valid_sources[allowed_sources:]
    neighbors: dict[str, list[dict]] = {}
    computed_pairs = 0
    for source_id in computed_ids:
        top_neighbors, pair_count = _build_top_neighbors_for_source(
            rows_by_entry_id[source_id], rows, max_neighbors
        )
        computed_pairs += pair_count
        neighbors[str(source_id)] = top_neighbors
    return neighbors, skipped_ids, computed_pairs


def get_similarity(db, entry_ids: list[int], k: int = 5, cache_path: Path | None = None) -> dict:
    """Return ``{results, meta}`` where results contain neighbor lists per requested entry_id."""
    if cache_path is None:
        cache_path = _CACHE_PATH

    k = min(max(int(k), 1), _MAX_K)
    wanted = _unique_entry_ids([int(v) for v in entry_ids if int(v) > 0])[:_MAX_REQUESTED_ENTRY_IDS]
    rows = _load_rows(db)
    embedding_count = len(rows)
    if embedding_count == 0:
        return {
            "results": [{"entry_id": eid, "neighbors": []} for eid in wanted],
            "meta": {
                "method": "cosine_knn",
                "k": k,
                "embedding_count": 0,
                "cached": False,
                "invalidation": "sha256(source_id,dimensions,vector,title,category)",
                "cache_scope": "per_entry_topk",
                "max_cached_k": _CACHE_NEIGHBORS,
            },
        }

    fingerprint = _fingerprint_rows(rows)
    cache = _load_cache(cache_path)
    cache_valid = bool(cache and cache.get("fingerprint") == fingerprint)
    all_neighbors = cache.get("neighbors", {}) if cache_valid else {}
    missing_entry_ids = [eid for eid in wanted if str(eid) not in all_neighbors]
    skipped_entry_ids: list[int] = []
    computed_pairs = 0
    if missing_entry_ids:
        computed_neighbors, skipped_entry_ids, computed_pairs = _compute_missing_neighbors(
            rows=rows,
            source_entry_ids=missing_entry_ids,
            max_neighbors=_CACHE_NEIGHBORS,
            max_pairs=_MAX_COMPUTE_PAIRS,
        )
        all_neighbors.update(computed_neighbors)
        _save_cache(
            {
                "fingerprint": fingerprint,
                "count": embedding_count,
                "neighbors": all_neighbors,
                "generated_at": int(time.time()),
                "max_cached_k": _CACHE_NEIGHBORS,
            },
            cache_path,
        )
    cached = cache_valid and not missing_entry_ids

    results = []
    for eid in wanted:
        all_for_entry = all_neighbors.get(str(eid), [])
        results.append({"entry_id": eid, "neighbors": all_for_entry[:k]})

    return {
        "results": results,
        "meta": {
            "method": "cosine_knn",
            "k": k,
            "embedding_count": embedding_count,
            "cached": cached,
            "invalidation": "sha256(source_id,dimensions,vector,title,category)",
            "fingerprint_prefix": fingerprint[:16],
            "cache_scope": "per_entry_topk",
            "max_cached_k": _CACHE_NEIGHBORS,
            "computed_pairs": computed_pairs,
            "degraded": bool(skipped_entry_ids),
            "skipped_entry_ids": skipped_entry_ids,
        },
    }
