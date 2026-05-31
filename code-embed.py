#!/usr/bin/env python3
"""sk code-embed — hybrid semantic code search over code_index.

Usage:
  sk code-embed index [path]           Embed unembedded code_index chunks
  sk code-embed search <query>         Hybrid BM25 + vector search with RRF
  sk code-embed status                 Show embedding coverage and provider status

Notes:
  - Uses Ollama via stdlib urllib.request when available.
  - Optionally uses fastembed for local embeddings.
  - Stores embeddings as packed float BLOBs (struct.pack) for portable storage.
  - Falls back to BM25-only search when no embedding provider is available.
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
import urllib.error
import urllib.request
from pathlib import Path

if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8")

try:
    from fastembed import TextEmbedding
except ImportError:
    TextEmbedding = None

try:
    import sqlite_vec
except ImportError:
    sqlite_vec = None

DB_PATH = Path(
    os.environ.get("SK_DB_PATH", str(Path.home() / ".copilot" / "session-state" / "knowledge.db"))
).expanduser()
OLLAMA_BASE = "http://localhost:11434"
OLLAMA_MODEL = "nomic-embed-text"
FASTEMBED_MODEL = "BAAI/bge-small-en-v1.5"
RRF_K = 60
CANDIDATE_K = 50
FTS_STRIP_RE = re.compile(r'\b(?:OR|AND|NOT|NEAR)\b|["*]', re.IGNORECASE)
_FASTEMBED_CACHE: dict[str, object] = {}


def _open_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _sanitize_fts(query: str) -> str:
    return " ".join(FTS_STRIP_RE.sub(" ", query).split())


def _make_project_id(path_text: str) -> str:
    path = Path(path_text).expanduser().resolve()
    return hashlib.sha256(str(path).encode()).hexdigest()[:16]


def _has_embedding_column(conn: sqlite3.Connection) -> bool:
    cols = conn.execute("PRAGMA table_info(code_index)").fetchall()
    return any(row[1] == "embedding" for row in cols)


def _has_ollama() -> bool:
    try:
        with urllib.request.urlopen(f"{OLLAMA_BASE}/api/tags", timeout=2) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, ValueError):
        return False


def _get_fastembed_encoder(model: str):
    if TextEmbedding is None:
        return None
    encoder = _FASTEMBED_CACHE.get(model)
    if encoder is None:
        encoder = TextEmbedding(model_name=model)
        _FASTEMBED_CACHE[model] = encoder
    return encoder


def _resolve_provider(requested: str | None) -> str | None:
    if requested == "ollama":
        return "ollama" if _has_ollama() else None
    if requested == "fastembed":
        return "fastembed" if TextEmbedding is not None else None
    if _has_ollama():
        return "ollama"
    if TextEmbedding is not None:
        return "fastembed"
    return None


def _post_json(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _embed_ollama(texts: list[str], model: str) -> list[list[float]]:
    payload = {"model": model or OLLAMA_MODEL, "input": texts}
    body = _post_json(f"{OLLAMA_BASE}/api/embed", payload)
    if isinstance(body.get("embeddings"), list):
        return body["embeddings"]
    embedding = body.get("embedding")
    return [embedding] if embedding else []


def _embed_fastembed(texts: list[str], model: str) -> list[list[float]]:
    encoder = _get_fastembed_encoder(model or FASTEMBED_MODEL)
    if encoder is None:
        return []
    return [list(vec) for vec in encoder.embed(texts)]


def _embed_texts(texts: list[str], provider: str | None, model: str) -> list[list[float]]:
    if not texts or provider is None:
        return []
    if provider == "ollama":
        return _embed_ollama(texts, model)
    if provider == "fastembed":
        return _embed_fastembed(texts, model)
    return []


def _pack_embedding(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *[float(value) for value in vector])


def _unpack_embedding(blob: bytes) -> tuple[float, ...]:
    dims = len(blob) // 4
    return struct.unpack(f"{dims}f", blob)


def _cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def _pending_rows(conn: sqlite3.Connection, project_id: str, batch_size: int) -> list[sqlite3.Row]:
    clauses = ["embedding IS NULL", "content_snippet != ''"]
    params: list[object] = []
    if project_id:
        clauses.append("project_id = ?")
        params.append(project_id)
    where_sql = " AND ".join(clauses)
    return conn.execute(
        f"""SELECT id, file_path, symbol_name, content_snippet
            FROM code_index WHERE {where_sql}
            ORDER BY id LIMIT ?""",
        [*params, batch_size],
    ).fetchall()


def _store_embeddings(conn: sqlite3.Connection, rows: list[sqlite3.Row], vectors: list[list[float]]) -> int:
    updates = []
    for row, vector in zip(rows, vectors, strict=False):
        if vector:
            updates.append((_pack_embedding(vector), row["id"]))
    conn.executemany("UPDATE code_index SET embedding = ? WHERE id = ?", updates)
    conn.commit()
    return len(updates)


def index_embeddings(path_text: str | None, provider_name: str | None, model: str, batch_size: int) -> int:
    project_id = _make_project_id(path_text) if path_text else ""
    provider = _resolve_provider(provider_name)
    if provider is None:
        print("No embedding provider available. Nothing indexed.")
        return 0
    conn = _open_db()
    try:
        if not _has_embedding_column(conn):
            print("code_index.embedding missing. Run: sk index migrate")
            return 0
        total = 0
        while True:
            rows = _pending_rows(conn, project_id, batch_size)
            if not rows:
                break
            vectors = _embed_texts([row["content_snippet"] for row in rows], provider, model)
            stored = _store_embeddings(conn, rows, vectors)
            if stored == 0:
                print("Provider returned no embeddings; stopping.")
                break
            total += stored
            print(f"Embedded {total} chunks...", flush=True)
        return total
    finally:
        conn.close()


def _bm25_rows(conn: sqlite3.Connection, query: str, project_id: str, language: str) -> list[dict]:
    clauses = []
    params: list[object] = []
    if project_id:
        clauses.append("ci.project_id = ?")
        params.append(project_id)
    if language:
        clauses.append("ci.language = ?")
        params.append(language)
    where_prefix = f"WHERE {' AND '.join(clauses)} AND " if clauses else "WHERE "
    safe = _sanitize_fts(query) or query.replace('"', "")
    fts_query = f'"{safe}"*' if " " not in safe else f'"{safe}"'
    sql = f"""SELECT ci.id, ci.file_path, ci.language, ci.symbol_name, ci.symbol_kind,
                     ci.start_line, ci.end_line, ci.content_snippet,
                     bm25(code_fts, 5.0, 1.0) AS bm25_score
              FROM code_fts JOIN code_index ci ON code_fts.rowid = ci.id
              {where_prefix}code_fts MATCH ?
              ORDER BY bm25_score LIMIT ?"""
    try:
        rows = conn.execute(sql, [*params, fts_query, CANDIDATE_K]).fetchall()
    except sqlite3.OperationalError:
        like = f"%{query.lower()}%"
        rows = conn.execute(
            f"""SELECT id, file_path, language, symbol_name, symbol_kind,
                         start_line, end_line, content_snippet, 0.0 AS bm25_score
                  FROM code_index {where_prefix}
                  (LOWER(symbol_name) LIKE ? OR LOWER(content_snippet) LIKE ?)
                  ORDER BY id LIMIT ?""",
            [*params, like, like, CANDIDATE_K],
        ).fetchall()
    return [dict(row) for row in rows]


def _vector_rows(conn: sqlite3.Connection, query_vec: tuple[float, ...], project_id: str, language: str) -> list[dict]:
    clauses = ["embedding IS NOT NULL"]
    params: list[object] = []
    if project_id:
        clauses.append("project_id = ?")
        params.append(project_id)
    if language:
        clauses.append("language = ?")
        params.append(language)
    sql = f"""SELECT id, file_path, language, symbol_name, symbol_kind,
                     start_line, end_line, content_snippet, embedding
              FROM code_index WHERE {" AND ".join(clauses)}"""
    ranked = []
    for row in conn.execute(sql, params).fetchall():
        score = _cosine_similarity(query_vec, _unpack_embedding(row["embedding"]))
        ranked.append({**dict(row), "vector_score": score})
    ranked.sort(key=lambda item: item["vector_score"], reverse=True)
    for item in ranked:
        item.pop("embedding", None)
    return ranked[:CANDIDATE_K]


def _rrf_fusion(bm25_rows: list[dict], vector_rows: list[dict], limit: int) -> list[dict]:
    merged: dict[int, dict] = {}
    for rank, row in enumerate(bm25_rows, start=1):
        item = dict(row)
        item["rrf_score"] = 1.0 / (rank + RRF_K)
        item["rank_bm25"] = rank
        merged[row["id"]] = item
    for rank, row in enumerate(vector_rows, start=1):
        item = merged.setdefault(row["id"], dict(row))
        item["rrf_score"] = item.get("rrf_score", 0.0) + 1.0 / (rank + RRF_K)
        item["rank_vec"] = rank
        item["vector_score"] = row.get("vector_score", 0.0)
    return sorted(merged.values(), key=lambda item: item["rrf_score"], reverse=True)[:limit]


def search_code(
    query: str, provider_name: str | None, model: str, project_id: str, language: str, limit: int
) -> list[dict]:
    conn = _open_db()
    try:
        bm25_rows = _bm25_rows(conn, query, project_id, language)
        if not _has_embedding_column(conn):
            return bm25_rows[:limit]
        has_embeddings = conn.execute("SELECT 1 FROM code_index WHERE embedding IS NOT NULL LIMIT 1").fetchone()
        provider = _resolve_provider(provider_name)
        if not has_embeddings or provider is None:
            return bm25_rows[:limit]
        query_vectors = _embed_texts([query], provider, model)
        if not query_vectors:
            return bm25_rows[:limit]
        vector_rows = _vector_rows(conn, tuple(query_vectors[0]), project_id, language)
        return _rrf_fusion(bm25_rows, vector_rows, limit)
    finally:
        conn.close()


def show_status(provider_name: str | None) -> None:
    conn = _open_db()
    try:
        total = conn.execute("SELECT COUNT(*) FROM code_index").fetchone()[0]
        has_embedding = _has_embedding_column(conn)
        embedded = (
            conn.execute("SELECT COUNT(*) FROM code_index WHERE embedding IS NOT NULL").fetchone()[0]
            if has_embedding
            else 0
        )
        pending = total - embedded if has_embedding else total
        sample = (
            conn.execute("SELECT embedding FROM code_index WHERE embedding IS NOT NULL LIMIT 1").fetchone()
            if has_embedding
            else None
        )
    finally:
        conn.close()
    provider = _resolve_provider(provider_name)
    dims = len(sample[0]) // 4 if sample else 0
    print(f"DB: {DB_PATH}")
    print(f"Chunks: {total}")
    print(f"Embedded: {embedded}")
    print(f"Pending: {pending}")
    print(f"Dimensions: {dims}")
    print(f"Provider: {provider or 'bm25-only'}")
    print(f"sqlite-vec available: {sqlite_vec is not None}")


def _default_model(provider_name: str | None, model: str | None) -> str:
    if model:
        return model
    resolved = _resolve_provider(provider_name)
    return OLLAMA_MODEL if resolved == "ollama" else FASTEMBED_MODEL


def _print_results(results: list[dict]) -> None:
    if not results:
        print("No results found.")
        return
    for row in results:
        print(f"\n{row['file_path']}:{row['start_line']}-{row['end_line']} [{row.get('language', '')}]")
        print(f"  {row.get('symbol_name', '')} ({row.get('symbol_kind', '')})")
        print(f"  RRF: {row.get('rrf_score', 0.0):.4f}")
        snippet = (row.get("content_snippet") or "").strip().splitlines()
        if snippet:
            print(f"  {snippet[0][:200]}")


def _add_provider_flags(parser: argparse.ArgumentParser, include_batch: bool = False) -> None:
    parser.add_argument("--provider", choices=["ollama", "fastembed"], help="Force embedding provider")
    parser.add_argument("--model", help="Override embedding model name")
    if include_batch:
        parser.add_argument("--batch-size", type=int, default=32, help="Chunks per embedding batch")


def main() -> None:
    parser = argparse.ArgumentParser(description="sk code-embed — hybrid semantic code search")
    subparsers = parser.add_subparsers(dest="command")

    index_parser = subparsers.add_parser("index", help="Embed code_index rows with embedding IS NULL")
    index_parser.add_argument("path", nargs="?", help="Optional repo path used to scope project_id")
    _add_provider_flags(index_parser, include_batch=True)

    search_parser = subparsers.add_parser("search", help="Hybrid BM25 + vector search")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--project", default="", help="Filter by project_id")
    search_parser.add_argument("--lang", default="", help="Filter by language")
    search_parser.add_argument("--limit", type=int, default=10, help="Max results")
    _add_provider_flags(search_parser)

    status_parser = subparsers.add_parser("status", help="Show embedding coverage and provider status")
    _add_provider_flags(status_parser)

    args = parser.parse_args()
    if args.command == "index":
        total = index_embeddings(args.path, args.provider, _default_model(args.provider, args.model), args.batch_size)
        print(f"Done. Embedded {total} chunks.")
        return
    if args.command == "search":
        results = search_code(
            args.query, args.provider, _default_model(args.provider, args.model), args.project, args.lang, args.limit
        )
        _print_results(results)
        return
    if args.command == "status":
        show_status(args.provider)
        return
    parser.print_help()
    raise SystemExit(1)


if __name__ == "__main__":
    main()
