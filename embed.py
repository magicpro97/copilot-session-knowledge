#!/usr/bin/env python3
"""
embed.py — Multi-provider embedding engine with hybrid search

Supports any OpenAI-compatible embedding API:
  - OpenAI (text-embedding-3-small)
  - Fireworks AI (nomic-ai/nomic-embed-text-v1.5) — very cheap
  - OpenRouter (routes to various providers)
  - Any custom endpoint (Ollama, LM Studio, vLLM, etc.)

Fallback: TF-IDF (scikit-learn) when no API key is available.
Storage: Regular SQLite table with blob vectors (zero extensions needed).

Usage:
    python embed.py --setup                    # Interactive provider setup
    python embed.py --build                    # Generate embeddings for all content
    python embed.py --search "query text"      # Semantic search
    python embed.py --status                   # Show embedding status
    python embed.py --providers                # List available providers
    python embed.py --test                     # Test current provider connectivity
"""

import json
import os
import sqlite3
import struct
import sys
import time
import urllib.request
import urllib.error
import ssl
from pathlib import Path
from math import sqrt

# Fix Windows console encoding
if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── Paths ──────────────────────────────────────────────────────────────
TOOLS_DIR = Path.home() / ".copilot" / "tools"
SESSION_STATE = Path.home() / ".copilot" / "session-state"
DB_PATH = SESSION_STATE / "knowledge.db"
CONFIG_PATH = TOOLS_DIR / "embedding-config.json"

# SSL verification — disable with --no-verify-ssl flag or COPILOT_NO_VERIFY_SSL=1
NO_VERIFY_SSL = False

# ── Default provider configs ──────────────────────────────────────────
DEFAULT_CONFIG = {
    "active_provider": "auto",  # "auto" tries env vars in order
    "fallback": "tfidf",        # "tfidf" or "none"
    "batch_size": 100,          # embeddings per API call (Fireworks supports up to 2048)
    "providers": {
        "openai": {
            "base_url": "https://api.openai.com/v1",
            "model": "text-embedding-3-small",
            "dimensions": 1536,
            "env_key": "OPENAI_API_KEY",
            "api_key": ""
        },
        "fireworks": {
            "base_url": "https://api.fireworks.ai/inference/v1",
            "model": "nomic-ai/nomic-embed-text-v1.5",
            "dimensions": 768,
            "env_key": "FIREWORKS_API_KEY",
            "api_key": ""
        },
        "openrouter": {
            "base_url": "https://openrouter.ai/api/v1",
            "model": "openai/text-embedding-3-small",
            "dimensions": 1536,
            "env_key": "OPENROUTER_API_KEY",
            "api_key": ""
        },
        "custom": {
            "base_url": "",
            "model": "",
            "dimensions": 768,
            "env_key": "EMBEDDING_API_KEY",
            "api_key": ""
        }
    }
}

# Provider priority for "auto" mode
AUTO_PRIORITY = ["fireworks", "openai", "openrouter", "custom"]


# ═══════════════════════════════════════════════════════════════════════
#  Configuration Management
# ═══════════════════════════════════════════════════════════════════════

def load_config() -> dict:
    """Load config from file, merging with defaults."""
    config = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    if CONFIG_PATH.exists():
        try:
            user_config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            # Merge providers
            for name, prov in user_config.get("providers", {}).items():
                if name in config["providers"]:
                    config["providers"][name].update(prov)
                else:
                    config["providers"][name] = prov
            # Merge top-level keys
            for key in ("active_provider", "fallback", "batch_size"):
                if key in user_config:
                    config[key] = user_config[key]
        except (json.JSONDecodeError, KeyError):
            pass
    _check_config_permissions()
    return config


def _check_config_permissions():
    """Warn if config file has overly permissive permissions."""
    if os.name == "nt" or not CONFIG_PATH.exists():
        return
    try:
        mode = CONFIG_PATH.stat().st_mode & 0o777
        if mode & 0o077:  # group or other has access
            print(f"⚠ {CONFIG_PATH} has permissive permissions ({oct(mode)}). "
                  f"Fixing to 0o600...", file=sys.stderr)
            os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def save_config(config: dict):
    """Save config to file with restrictive permissions."""
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(config, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    # Restrict file permissions to owner-only (not world-readable)
    if os.name != "nt":
        os.chmod(CONFIG_PATH, 0o600)


def get_api_key(provider_config: dict) -> str:
    """Get API key from environment variable or config file."""
    # Prefer environment variable (more secure than config file)
    env_key = provider_config.get("env_key", "")
    if env_key:
        env_val = os.environ.get(env_key, "")
        if env_val:
            return env_val
    # Fall back to config file key
    key = provider_config.get("api_key", "")
    if key:
        return key
    return ""


def resolve_provider(config: dict) -> tuple:
    """Resolve which provider to use. Returns (name, provider_config) or (None, None)."""
    active = config.get("active_provider", "auto")

    if active != "auto":
        prov = config["providers"].get(active)
        if prov and get_api_key(prov):
            return active, prov
        return None, None

    # Auto mode: try providers in priority order
    for name in AUTO_PRIORITY:
        prov = config["providers"].get(name)
        if prov and get_api_key(prov) and prov.get("base_url"):
            return name, prov

    return None, None


# ═══════════════════════════════════════════════════════════════════════
#  Embedding API (OpenAI-compatible, stdlib only)
# ═══════════════════════════════════════════════════════════════════════

class EmbeddingAuthError(RuntimeError):
    """Auth error — should fallback to TF-IDF, not retry."""
    pass


class EmbeddingRateLimitError(RuntimeError):
    """Rate limit — should retry with backoff."""
    pass


class EmbeddingNetworkError(RuntimeError):
    """Network/timeout — should retry then fallback."""
    pass


def _classify_api_error(e: urllib.error.HTTPError) -> tuple[str, str]:
    """Classify HTTP error into (category, user_message). Categories: auth, rate_limit, server."""
    body = e.read().decode("utf-8", errors="replace")[:500]
    if e.code in (401, 403):
        return "auth", f"🔑 Auth failed ({e.code}): API key invalid or expired. Falling back to TF-IDF."
    elif e.code == 429:
        return "rate_limit", f"⏳ Rate limited (429). Retrying with backoff..."
    elif e.code >= 500:
        return "server", f"🔥 Server error ({e.code}). Retrying..."
    elif e.code == 404:
        return "auth", f"🔍 Model not found (404): Check model name in config. {body[:100]}"
    else:
        return "server", f"❌ API error {e.code}: {body[:200]}"


def call_embedding_api(texts: list[str], provider_config: dict,
                       max_retries: int = 3) -> list[list[float]]:
    """Call an OpenAI-compatible embedding API with classified error handling and retry."""
    base_url = provider_config["base_url"].rstrip("/")
    model = provider_config["model"]
    api_key = get_api_key(provider_config)
    dimensions = provider_config.get("dimensions")

    url = f"{base_url}/embeddings"

    payload = {
        "input": texts,
        "model": model,
    }
    if dimensions:
        payload["dimensions"] = dimensions

    data = json.dumps(payload).encode("utf-8")

    ssl_ctx = None
    if NO_VERIFY_SSL:
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

    last_error = None
    for attempt in range(max_retries):
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("User-Agent", "copilot-session-tools/1.0")

        try:
            with urllib.request.urlopen(req, timeout=120, context=ssl_ctx) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            category, message = _classify_api_error(e)
            if category == "auth":
                # Auth errors: no retry, raise specific exception for fallback
                print(f"    {message}", file=sys.stderr)
                raise EmbeddingAuthError(message)
            elif category == "rate_limit":
                last_error = message
                wait = min((2 ** attempt) + 1, 30)
                print(f"    {message} ({wait}s)", file=sys.stderr)
                time.sleep(wait)
                continue
            else:  # server error
                last_error = message
                wait = (2 ** attempt) + 1
                print(f"    {message} Retry {attempt+1}/{max_retries} in {wait}s",
                      file=sys.stderr)
                time.sleep(wait)
                continue
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            reason = getattr(e, "reason", str(e))
            last_error = f"🌐 Network error: {reason}"
            wait = (2 ** attempt) + 1
            print(f"    {last_error} Retry {attempt+1}/{max_retries} in {wait}s",
                  file=sys.stderr)
            time.sleep(wait)
            continue
    else:
        if "Rate limit" in (last_error or ""):
            raise EmbeddingRateLimitError(last_error)
        elif "Network" in (last_error or "") or "Connection" in (last_error or ""):
            raise EmbeddingNetworkError(last_error or "Max retries exceeded")
        raise RuntimeError(last_error or "Max retries exceeded")

    # Parse OpenAI-format response
    embeddings = []
    for item in sorted(result.get("data", []), key=lambda x: x.get("index", 0)):
        embeddings.append(item["embedding"])

    if len(embeddings) != len(texts):
        raise RuntimeError(f"Expected {len(texts)} embeddings, got {len(embeddings)}")

    return embeddings


def embed_batch(texts: list[str], config: dict, provider_name: str = None,
                provider_config: dict = None) -> list[list[float]]:
    """Embed a batch of texts, respecting batch_size limit."""
    if not provider_name or not provider_config:
        provider_name, provider_config = resolve_provider(config)
    if not provider_config:
        raise RuntimeError("No embedding provider configured. Run: python embed.py --setup")

    batch_size = config.get("batch_size", 100)
    all_embeddings = []
    total = len(texts)
    num_batches = (total + batch_size - 1) // batch_size

    for i in range(0, total, batch_size):
        chunk = texts[i:i + batch_size]
        batch_num = i // batch_size + 1
        print(f"    batch {batch_num}/{num_batches} ({len(chunk)} items)...", end="", flush=True)
        embeddings = call_embedding_api(chunk, provider_config)
        all_embeddings.extend(embeddings)
        print(" ✓")
        if i + batch_size < total:
            time.sleep(0.3)  # rate limit courtesy

    return all_embeddings


# ═══════════════════════════════════════════════════════════════════════
#  TF-IDF Fallback (optional, requires scikit-learn)
# ═══════════════════════════════════════════════════════════════════════

def tfidf_available() -> bool:
    """Check if scikit-learn is available."""
    try:
        import sklearn  # noqa: F401
        return True
    except ImportError:
        return False


def build_tfidf(texts: list[str], doc_ids: list[int]) -> bytes:
    """Build TF-IDF model and return serialized (vectorizer params, matrix, doc_ids)."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from scipy.sparse import coo_matrix

    vectorizer = TfidfVectorizer(
        max_features=8000,
        ngram_range=(1, 2),
        sublinear_tf=True,
        strip_accents="unicode",
        min_df=1,
        max_df=0.95,
    )
    matrix = vectorizer.fit_transform(texts)
    coo = coo_matrix(matrix)

    model = {
        "vocabulary": {k: int(v) for k, v in vectorizer.vocabulary_.items()},
        "idf": vectorizer.idf_.tolist(),
        "matrix_row": coo.row.tolist(),
        "matrix_col": coo.col.tolist(),
        "matrix_data": coo.data.tolist(),
        "matrix_shape": [int(x) for x in coo.shape],
        "doc_ids": [int(x) for x in doc_ids],
        "params": {
            "max_features": 8000,
            "ngram_range": [1, 2],
            "sublinear_tf": True,
            "strip_accents": "unicode",
            "min_df": 1,
            "max_df": 0.95,
        },
    }
    return json.dumps(model).encode("utf-8")


def search_tfidf(query: str, model_blob: bytes, limit: int = 10) -> list[tuple]:
    """Search TF-IDF model. Returns [(doc_id, score), ...]."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    # Reject old pickle format — unsafe deserialization
    if model_blob[:2] in (b'\x80\x04', b'\x80\x05'):
        print("⚠ TF-IDF model uses deprecated pickle format (unsafe). "
              "Re-run embedding to upgrade: python embed.py --rebuild-tfidf",
              file=sys.stderr)
        return []

    # New JSON format
    from scipy.sparse import csr_matrix
    import numpy as np

    model = json.loads(model_blob.decode("utf-8"))

    # Validate required keys
    required = {"vocabulary", "idf", "matrix_row", "matrix_col", "matrix_data", "matrix_shape", "doc_ids"}
    if not required.issubset(model.keys()):
        raise ValueError("Invalid TF-IDF model format")

    # Reconstruct vectorizer
    vectorizer = TfidfVectorizer(
        max_features=model.get("params", {}).get("max_features", 8000),
        ngram_range=tuple(model.get("params", {}).get("ngram_range", [1, 2])),
        sublinear_tf=model.get("params", {}).get("sublinear_tf", True),
        strip_accents=model.get("params", {}).get("strip_accents", "unicode"),
        min_df=model.get("params", {}).get("min_df", 1),
        max_df=model.get("params", {}).get("max_df", 0.95),
    )
    vectorizer.vocabulary_ = model["vocabulary"]
    vectorizer.idf_ = np.array(model["idf"])
    vectorizer._tfidf._idf_diag = csr_matrix(np.diag(vectorizer.idf_))

    # Reconstruct matrix
    shape = tuple(model["matrix_shape"])
    matrix = csr_matrix(
        (model["matrix_data"], (model["matrix_row"], model["matrix_col"])),
        shape=shape
    )

    doc_ids = model["doc_ids"]
    query_vec = vectorizer.transform([query])
    scores = cosine_similarity(query_vec, matrix).flatten()
    top_idx = scores.argsort()[::-1][:limit]
    return [(doc_ids[i], float(scores[i])) for i in top_idx if scores[i] > 0.01]


# ═══════════════════════════════════════════════════════════════════════
#  Vector Storage (plain SQLite, no extensions needed)
# ═══════════════════════════════════════════════════════════════════════

def serialize_vector(vec: list[float]) -> bytes:
    """Serialize a float vector to bytes (little-endian float32)."""
    return struct.pack(f"<{len(vec)}f", *vec)


def deserialize_vector(blob: bytes) -> list[float]:
    """Deserialize bytes to float vector."""
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def cosine_similarity_vectors(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors (pure Python)."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sqrt(sum(x * x for x in a))
    norm_b = sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def ensure_embedding_tables(db: sqlite3.Connection):
    """Create embedding tables if they don't exist."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            vector BLOB NOT NULL,
            text_preview TEXT DEFAULT '',
            created_at TEXT,
            UNIQUE(source_type, source_id)
        );

        CREATE INDEX IF NOT EXISTS idx_emb_source
            ON embeddings(source_type, source_id);

        CREATE TABLE IF NOT EXISTS embedding_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS tfidf_model (
            id INTEGER PRIMARY KEY DEFAULT 1,
            model_blob BLOB,
            doc_count INTEGER DEFAULT 0,
            built_at TEXT
        );
    """)


def store_embeddings(db: sqlite3.Connection, source_type: str,
                     items: list[tuple], provider: str, model: str,
                     dimensions: int):
    """Store embeddings in DB. items = [(source_id, vector, text_preview), ...]"""
    ensure_embedding_tables(db)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")

    for source_id, vector, preview in items:
        blob = serialize_vector(vector)
        db.execute("""
            INSERT OR REPLACE INTO embeddings
                (source_type, source_id, provider, model, dimensions, vector,
                 text_preview, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (source_type, source_id, provider, model, dimensions, blob,
              preview[:200], now))

    db.execute("""
        INSERT OR REPLACE INTO embedding_meta (key, value)
        VALUES ('last_build', ?)
    """, (now,))
    db.commit()


def vector_search(db: sqlite3.Connection, query_vector: list[float],
                  source_type: str = None, limit: int = 20) -> list[tuple]:
    """Brute-force cosine similarity search. Returns [(source_type, source_id, score), ...]"""
    sql = "SELECT source_type, source_id, vector FROM embeddings"
    params = []
    if source_type:
        sql += " WHERE source_type = ?"
        params.append(source_type)

    rows = db.execute(sql, params).fetchall()
    if not rows:
        return []

    results = []
    for st, sid, blob in rows:
        vec = deserialize_vector(blob)
        score = cosine_similarity_vectors(query_vector, vec)
        results.append((st, sid, score))

    results.sort(key=lambda x: -x[2])
    return results[:limit]


# ═══════════════════════════════════════════════════════════════════════
#  Hybrid Search: FTS5 + Vector + RRF
# ═══════════════════════════════════════════════════════════════════════

def reciprocal_rank_fusion(ranked_lists: list[list], k: int = 60) -> list[tuple]:
    """
    Merge multiple ranked lists using Reciprocal Rank Fusion.
    Each list contains keys (any hashable type).
    Returns [(key, rrf_score), ...] sorted by score desc.
    """
    scores = {}
    for ranked in ranked_lists:
        for rank, key in enumerate(ranked):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: -x[1])


def hybrid_search(db: sqlite3.Connection, query: str, config: dict,
                  limit: int = 10, fts_weight: float = 1.0,
                  vec_weight: float = 1.0) -> list[dict]:
    """
    Hybrid search combining FTS5 keyword search + vector semantic search.
    Returns merged results with source info.
    """
    results_fts = []
    results_vec = []

    # ── FTS5 search ──
    fts_query = query.strip()
    if not any(c in fts_query for c in ['"', "*", "OR", "AND", "NOT", "NEAR"]):
        terms = fts_query.split()
        fts_query = " ".join(f'"{t}"*' for t in terms)

    try:
        fts_rows = db.execute("""
            SELECT fts.document_id, fts.title, fts.section_name, fts.doc_type,
                   fts.session_id,
                   snippet(knowledge_fts, 2, '>>>', '<<<', '...', 64) as excerpt,
                   rank
            FROM knowledge_fts fts
            WHERE knowledge_fts MATCH ?
            ORDER BY rank
            LIMIT 30
        """, (fts_query,)).fetchall()

        for r in fts_rows:
            key = ("section", r[0], r[2])  # (type, doc_id, section_name)
            results_fts.append((key, {
                "document_id": r[0], "title": r[1], "section_name": r[2],
                "doc_type": r[3], "session_id": r[4], "excerpt": r[5],
                "fts_rank": r[6], "source": "keyword"
            }))
    except sqlite3.OperationalError:
        pass

    # ── Vector search ──
    query_vector = None
    provider_name, provider_config = resolve_provider(config)

    if provider_name and provider_config:
        try:
            vecs = call_embedding_api([query], provider_config)
            query_vector = vecs[0]
        except EmbeddingAuthError:
            pass  # Silent fallback — FTS results will be used
        except (EmbeddingRateLimitError, EmbeddingNetworkError):
            pass  # Transient — silently use FTS only
        except Exception as e:
            print(f"  [warn] Embedding API error: {e}", file=sys.stderr)

    if query_vector:
        vec_results = vector_search(db, query_vector, limit=30)
        for st, sid, score in vec_results:
            if score < 0.1:
                continue
            # Look up document info
            if st == "section":
                row = db.execute("""
                    SELECT s.document_id, d.title, s.section_name, d.doc_type,
                           d.session_id, SUBSTR(s.content, 1, 200) as excerpt
                    FROM sections s
                    JOIN documents d ON s.document_id = d.id
                    WHERE s.id = ?
                """, (sid,)).fetchone()
                if row:
                    key = ("section", row[0], row[2])
                    results_vec.append((key, {
                        "document_id": row[0], "title": row[1],
                        "section_name": row[2], "doc_type": row[3],
                        "session_id": row[4], "excerpt": row[5],
                        "vec_score": score, "source": "semantic"
                    }))
            elif st == "knowledge":
                row = db.execute("""
                    SELECT ke.id, ke.title, ke.category, ke.session_id,
                           SUBSTR(ke.content, 1, 200) as excerpt
                    FROM knowledge_entries ke WHERE ke.id = ?
                """, (sid,)).fetchone()
                if row:
                    key = ("knowledge", row[0], row[2])
                    results_vec.append((key, {
                        "document_id": row[0], "title": row[1],
                        "doc_type": row[2], "session_id": row[3],
                        "excerpt": row[4], "vec_score": score,
                        "source": "semantic"
                    }))
    elif config.get("fallback") == "tfidf" and tfidf_available():
        # TF-IDF fallback
        try:
            ensure_embedding_tables(db)
            row = db.execute(
                "SELECT model_blob FROM tfidf_model WHERE id = 1"
            ).fetchone()
            if row and row[0]:
                tfidf_results = search_tfidf(query, row[0], limit=30)
                for section_id, score in tfidf_results:
                    if score < 0.05:
                        continue
                    info = db.execute("""
                        SELECT s.document_id, d.title, s.section_name, d.doc_type,
                               d.session_id, SUBSTR(s.content, 1, 200) as excerpt
                        FROM sections s
                        JOIN documents d ON s.document_id = d.id
                        WHERE s.id = ?
                    """, (section_id,)).fetchone()
                    if info:
                        key = ("section", info[0], info[2])
                        results_vec.append((key, {
                            "document_id": info[0], "title": info[1],
                            "section_name": info[2], "doc_type": info[3],
                            "session_id": info[4], "excerpt": info[5],
                            "tfidf_score": score, "source": "tfidf"
                        }))
        except (sqlite3.OperationalError, Exception):
            pass

    # ── Merge with RRF ──
    fts_keys = [item[0] for item in results_fts]
    vec_keys = [item[0] for item in results_vec]

    # Build lookup dict
    info_map = {}
    for key, info in results_fts + results_vec:
        if key not in info_map:
            info_map[key] = info
        else:
            # Merge sources
            existing = info_map[key]
            if "source" in info:
                sources = set(existing.get("source", "").split("+"))
                sources.add(info["source"])
                existing["source"] = "+".join(sorted(s for s in sources if s))
            for k in ("vec_score", "tfidf_score", "fts_rank"):
                if k in info:
                    existing[k] = info[k]

    if fts_keys and vec_keys:
        merged = reciprocal_rank_fusion([fts_keys, vec_keys])
    elif fts_keys:
        merged = [(k, 1.0 / (60 + i + 1)) for i, k in enumerate(fts_keys)]
    elif vec_keys:
        merged = [(k, 1.0 / (60 + i + 1)) for i, k in enumerate(vec_keys)]
    else:
        return []

    results = []
    for key, rrf_score in merged[:limit]:
        info = info_map.get(key, {})
        info["rrf_score"] = rrf_score
        results.append(info)

    return results


# ═══════════════════════════════════════════════════════════════════════
#  Build Embeddings
# ═══════════════════════════════════════════════════════════════════════

def build_embeddings(config: dict = None, force: bool = False):
    """Generate embeddings for all indexed content."""
    if config is None:
        config = load_config()

    if not DB_PATH.exists():
        print("Error: Knowledge database not found. Run build-session-index.py first.")
        return False

    db = sqlite3.connect(str(DB_PATH))
    ensure_embedding_tables(db)

    # Gather all sections
    sections = db.execute("""
        SELECT s.id, d.title || ' - ' || s.section_name as label,
               s.content
        FROM sections s
        JOIN documents d ON s.document_id = d.id
    """).fetchall()

    # Gather knowledge entries
    ke_rows = db.execute("""
        SELECT id, title, content
        FROM knowledge_entries
    """).fetchall()

    print(f"Content: {len(sections)} sections, {len(ke_rows)} knowledge entries")

    # Check what's already embedded
    if not force:
        existing = set()
        for row in db.execute("SELECT source_type, source_id FROM embeddings"):
            existing.add((row[0], row[1]))
        new_sections = [(s[0], s[1], s[2]) for s in sections
                        if ("section", s[0]) not in existing]
        new_ke = [(k[0], k[1], k[2]) for k in ke_rows
                  if ("knowledge", k[0]) not in existing]
    else:
        new_sections = [(s[0], s[1], s[2]) for s in sections]
        new_ke = [(k[0], k[1], k[2]) for k in ke_rows]
        db.execute("DELETE FROM embeddings")

    total_new = len(new_sections) + len(new_ke)
    if total_new == 0:
        print("All content already embedded. Use --force to rebuild.")
        db.close()
        return True

    print(f"New content to embed: {len(new_sections)} sections + {len(new_ke)} entries")

    # Resolve provider
    provider_name, provider_config = resolve_provider(config)

    api_failed = False
    if provider_name and provider_config:
        print(f"Provider: {provider_name} ({provider_config['model']})")
        dimensions = provider_config.get("dimensions", 768)

        # Embed sections
        if new_sections:
            print(f"Embedding {len(new_sections)} sections...")
            texts = [f"{label}: {content[:2000]}" for _, label, content in new_sections]
            try:
                vectors = embed_batch(texts, config, provider_name, provider_config)
                items = [
                    (new_sections[i][0], vectors[i], new_sections[i][1])
                    for i in range(len(vectors))
                ]
                store_embeddings(db, "section", items, provider_name,
                                 provider_config["model"], dimensions)
                print(f"  ✓ {len(vectors)} section embeddings stored")
            except EmbeddingAuthError:
                print(f"  ⚠ Auth failed — skipping API embeddings, using TF-IDF fallback")
                api_failed = True
            except (EmbeddingRateLimitError, EmbeddingNetworkError) as e:
                print(f"  ⚠ {e} — falling back to TF-IDF")
                api_failed = True
            except Exception as e:
                print(f"  ✗ Section embedding failed: {e}")

        # Embed knowledge entries (skip if auth failed)
        if new_ke and not api_failed:
            print(f"Embedding {len(new_ke)} knowledge entries...")
            texts = [f"{title}: {content[:2000]}" for _, title, content in new_ke]
            try:
                vectors = embed_batch(texts, config, provider_name, provider_config)
                items = [
                    (new_ke[i][0], vectors[i], new_ke[i][1])
                    for i in range(len(vectors))
                ]
                store_embeddings(db, "knowledge", items, provider_name,
                                 provider_config["model"], dimensions)
                print(f"  ✓ {len(vectors)} knowledge embeddings stored")
            except EmbeddingAuthError:
                print(f"  ⚠ Auth failed — using TF-IDF fallback")
                api_failed = True
            except (EmbeddingRateLimitError, EmbeddingNetworkError) as e:
                print(f"  ⚠ {e} — falling back to TF-IDF")
                api_failed = True
            except Exception as e:
                print(f"  ✗ Knowledge embedding failed: {e}")

    else:
        print("No API provider configured. Using TF-IDF fallback only.")

    # Always build TF-IDF as fallback (if available)
    if tfidf_available():
        print("Building TF-IDF fallback model...")
        all_texts = [s[2][:3000] for s in sections]
        all_ids = [s[0] for s in sections]
        try:
            model_blob = build_tfidf(all_texts, all_ids)
            db.execute("""
                INSERT OR REPLACE INTO tfidf_model (id, model_blob, doc_count, built_at)
                VALUES (1, ?, ?, ?)
            """, (model_blob, len(all_texts), time.strftime("%Y-%m-%dT%H:%M:%S")))
            db.commit()
            print(f"  ✓ TF-IDF model built ({len(all_texts)} documents)")
        except Exception as e:
            print(f"  ✗ TF-IDF build failed: {e}")
    else:
        print("  [info] scikit-learn not installed, TF-IDF fallback skipped")
        print("         Install with: pip install scikit-learn")

    db.close()
    return True


# ═══════════════════════════════════════════════════════════════════════
#  CLI Commands
# ═══════════════════════════════════════════════════════════════════════

def cmd_setup():
    """Interactive provider setup."""
    config = load_config()

    print("\n═══ Embedding Provider Setup ═══\n")
    print("Choose a provider for generating embeddings:")
    print()
    print("  1. fireworks   — Fireworks AI (nomic-embed, $0.008/1M tokens)")
    print("  2. openai      — OpenAI (text-embedding-3-small, $0.02/1M tokens)")
    print("  3. openrouter  — OpenRouter (routes to various providers)")
    print("  4. custom      — Any OpenAI-compatible endpoint (Ollama, LM Studio...)")
    print("  5. auto        — Auto-detect from environment variables")
    print()

    choice = input("Select provider [1-5, default=5]: ").strip() or "5"
    provider_map = {"1": "fireworks", "2": "openai", "3": "openrouter",
                    "4": "custom", "5": "auto"}
    provider_name = provider_map.get(choice, "auto")

    if provider_name == "auto":
        config["active_provider"] = "auto"
        print("\nAuto mode: will check environment variables in order:")
        for name in AUTO_PRIORITY:
            env_key = config["providers"][name]["env_key"]
            has_key = "✓" if os.environ.get(env_key) else "✗"
            print(f"  {has_key} {env_key} → {name}")
    else:
        config["active_provider"] = provider_name
        prov = config["providers"][provider_name]

        if provider_name == "custom":
            prov["base_url"] = input(f"Base URL [{prov['base_url'] or 'http://localhost:11434/v1'}]: ").strip() or prov.get("base_url") or "http://localhost:11434/v1"
            prov["model"] = input(f"Model name [{prov['model'] or 'nomic-embed-text'}]: ").strip() or prov.get("model") or "nomic-embed-text"
            dims = input(f"Dimensions [{prov['dimensions']}]: ").strip()
            if dims:
                prov["dimensions"] = int(dims)

        # API key
        env_key = prov.get("env_key", "")
        env_val = os.environ.get(env_key, "")
        if env_val:
            print(f"\n  ✓ API key found in ${env_key}")
            use_env = input("  Use environment variable? [Y/n]: ").strip().lower()
            if use_env == "n":
                prov["api_key"] = input("  Enter API key: ").strip()
        else:
            print(f"\n  No ${env_key} environment variable found.")
            prov["api_key"] = input("  Enter API key (or leave empty to set env var later): ").strip()

    # Fallback
    print()
    if tfidf_available():
        print("  ✓ scikit-learn detected — TF-IDF fallback available")
        config["fallback"] = "tfidf"
    else:
        print("  ℹ scikit-learn not installed — no TF-IDF fallback")
        print("    Install with: pip install scikit-learn")
        config["fallback"] = "none"

    save_config(config)
    print(f"\n  ✓ Config saved to {CONFIG_PATH}")

    # Test connectivity
    print("\nTesting provider connectivity...")
    cmd_test(config)


def cmd_test(config: dict = None):
    """Test embedding provider connectivity."""
    if config is None:
        config = load_config()

    name, prov = resolve_provider(config)
    if not name:
        print("  ✗ No provider configured or API key missing.")
        print("    Run: python embed.py --setup")
        return False

    print(f"  Provider: {name}")
    print(f"  Model: {prov['model']}")
    print(f"  Endpoint: {prov['base_url']}/embeddings")

    try:
        start = time.time()
        vecs = call_embedding_api(["Hello, this is a test."], prov)
        elapsed = time.time() - start
        dim = len(vecs[0])
        print(f"  ✓ Success! Got {dim}-dimensional vector in {elapsed:.2f}s")
        return True
    except Exception as e:
        print(f"  ✗ Failed: {e}")
        return False


def cmd_status():
    """Show embedding status."""
    config = load_config()

    print("\n═══ Embedding Status ═══\n")

    # Provider
    name, prov = resolve_provider(config)
    if name:
        print(f"Active provider: {name} ({prov['model']})")
    else:
        print(f"Active provider: none configured")
        print(f"  Tip: Run 'python embed.py --setup' or set an env var:")
        for pname in AUTO_PRIORITY:
            p = config["providers"][pname]
            print(f"    export {p['env_key']}=your-key  # → {pname}")

    # Fallback
    fallback = config.get("fallback", "none")
    if fallback == "tfidf":
        if tfidf_available():
            print(f"Fallback: TF-IDF (scikit-learn) ✓")
        else:
            print(f"Fallback: TF-IDF (scikit-learn NOT installed)")
    else:
        print(f"Fallback: none")

    # DB stats
    if not DB_PATH.exists():
        print("\nKnowledge DB: not found")
        return

    db = sqlite3.connect(str(DB_PATH))
    ensure_embedding_tables(db)

    emb_count = db.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    sec_count = db.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
    ke_count = 0
    try:
        ke_count = db.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
    except sqlite3.OperationalError:
        pass

    total_content = sec_count + ke_count
    print(f"\nEmbeddings: {emb_count}/{total_content} content items")

    if emb_count > 0:
        row = db.execute(
            "SELECT provider, model, dimensions, MIN(created_at), MAX(created_at) FROM embeddings"
        ).fetchone()
        print(f"  Provider: {row[0]}, Model: {row[1]}, Dims: {row[2]}")
        print(f"  Built: {row[3]} → {row[4]}")

    # TF-IDF
    try:
        tfidf = db.execute("SELECT doc_count, built_at FROM tfidf_model WHERE id=1").fetchone()
        if tfidf and tfidf[0]:
            print(f"\nTF-IDF model: {tfidf[0]} documents (built {tfidf[1]})")
    except sqlite3.OperationalError:
        pass

    db.close()


def cmd_providers():
    """List all configured providers."""
    config = load_config()

    print("\n═══ Configured Providers ═══\n")
    active = config.get("active_provider", "auto")

    for name, prov in config["providers"].items():
        has_key = bool(get_api_key(prov))
        marker = "→" if (active == name or (active == "auto" and has_key)) else " "
        key_status = "✓ key" if has_key else "✗ no key"
        url = prov.get("base_url", "")[:50]

        print(f"  {marker} {name:12s}  {key_status:8s}  {prov.get('model', ''):35s}  {url}")

    print(f"\n  Active: {active}")
    if active == "auto":
        name, _ = resolve_provider(config)
        print(f"  Auto-resolved: {name or 'none (no keys found)'}")


def cmd_search(query: str, limit: int = 10):
    """Semantic search from CLI."""
    config = load_config()

    if not DB_PATH.exists():
        print("Error: Knowledge database not found.")
        return

    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    ensure_embedding_tables(db)

    results = hybrid_search(db, query, config, limit=limit)

    if not results:
        print(f"No results for: {query}")
        db.close()
        return

    print(f"\nHybrid search: {len(results)} results for '{query}'\n")

    for i, r in enumerate(results, 1):
        sid = r.get("session_id", "?")[:8]
        source = r.get("source", "?")
        rrf = r.get("rrf_score", 0)

        print(f"  {i}. {r.get('title', '?')}")
        print(f"     {sid}.. | {r.get('doc_type', '?')} | {source} | rrf={rrf:.4f}")

        excerpt = r.get("excerpt", "")[:150]
        if excerpt:
            print(f"     {excerpt}")
        print()

    db.close()


def main():
    global NO_VERIFY_SSL
    args = sys.argv[1:]

    if not args or "--help" in args or "-h" in args:
        print(__doc__)
        return

    if "--no-verify-ssl" in args or os.environ.get("COPILOT_NO_VERIFY_SSL"):
        NO_VERIFY_SSL = True
        args = [a for a in args if a != "--no-verify-ssl"]

    if "--setup" in args:
        cmd_setup()
    elif "--build" in args:
        force = "--force" in args
        build_embeddings(force=force)
    elif "--search" in args:
        idx = args.index("--search")
        query = args[idx + 1] if idx + 1 < len(args) else ""
        if not query:
            print("Error: --search requires a query string")
            return
        limit = 10
        if "--limit" in args:
            li = args.index("--limit")
            limit = int(args[li + 1]) if li + 1 < len(args) else 10
        cmd_search(query, limit)
    elif "--status" in args:
        cmd_status()
    elif "--providers" in args:
        cmd_providers()
    elif "--test" in args:
        cmd_test()
    else:
        # Treat remaining args as search query
        query = " ".join(a for a in args if not a.startswith("--"))
        if query:
            cmd_search(query)
        else:
            print(__doc__)


if __name__ == "__main__":
    main()
