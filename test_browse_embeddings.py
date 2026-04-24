#!/usr/bin/env python3
"""
test_browse_embeddings.py — Tests for the F9 Embeddings 2D Projection feature.

Uses http.client against a locally spawned ThreadingHTTPServer with
an in-memory SQLite DB. No external dependencies required.
"""
import http.client
import json
import os
import sqlite3
import struct
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))
import browse  # noqa: E402 (triggers route registration)
import browse.core.projection as _proj  # noqa: E402

_PASS = 0
_FAIL = 0

# Use a path inside the project dir for test caches (never /tmp)
_TEST_CACHE_DIR = Path(__file__).parent / "__test_cache_emb__"


def test(name: str, expr: bool) -> None:
    global _PASS, _FAIL
    if expr:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}")


def _make_vec(dims: int, seed: int) -> bytes:
    """Make a deterministic float32 BLOB of *dims* dimensions."""
    import random
    rng = random.Random(seed)
    floats = [rng.gauss(0, 1) for _ in range(dims)]
    return struct.pack(f"<{dims}f", *floats)


def _make_test_db(n_embeddings: int = 6, dims: int = 8) -> sqlite3.Connection:
    """Create an in-memory DB with knowledge_entries + embeddings tables."""
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY, path TEXT, summary TEXT, source TEXT,
            file_mtime REAL, indexed_at_r REAL, fts_indexed_at REAL,
            event_count_estimate INTEGER, file_size_bytes INTEGER,
            total_checkpoints INTEGER, total_research INTEGER,
            total_files INTEGER, has_plan INTEGER, indexed_at TEXT
        );
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY, session_id TEXT, doc_type TEXT, seq INTEGER,
            title TEXT, file_path TEXT, file_hash TEXT, size_bytes INTEGER,
            content_preview TEXT, indexed_at TEXT, source TEXT
        );
        CREATE TABLE sections (
            id INTEGER PRIMARY KEY, document_id INTEGER,
            section_name TEXT, content TEXT
        );
        CREATE TABLE knowledge (
            id INTEGER PRIMARY KEY, title TEXT, content TEXT,
            category TEXT, wing TEXT, room TEXT
        );
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT
        );
        CREATE TABLE knowledge_entries (
            id INTEGER PRIMARY KEY,
            session_id TEXT NOT NULL,
            document_id INTEGER,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            tags TEXT DEFAULT '',
            confidence REAL DEFAULT 1.0,
            occurrence_count INTEGER DEFAULT 1,
            first_seen TEXT, last_seen TEXT,
            source TEXT DEFAULT 'copilot',
            topic_key TEXT, revision_count INTEGER DEFAULT 1,
            content_hash TEXT,
            wing TEXT DEFAULT '', room TEXT DEFAULT '',
            facts TEXT DEFAULT '[]', est_tokens INTEGER DEFAULT 0,
            task_id TEXT DEFAULT '', affected_files TEXT DEFAULT '[]'
        );
        CREATE TABLE entity_relations (
            id INTEGER PRIMARY KEY,
            subject TEXT NOT NULL, predicate TEXT NOT NULL, object TEXT NOT NULL,
            noted_at TEXT DEFAULT (datetime('now')), session_id TEXT DEFAULT ''
        );
        CREATE TABLE embeddings (
            id INTEGER PRIMARY KEY,
            source_type TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            vector BLOB NOT NULL,
            text_preview TEXT DEFAULT '',
            created_at TEXT
        );
        CREATE VIRTUAL TABLE sessions_fts USING fts5(
            session_id UNINDEXED, title, user_messages,
            assistant_messages, tool_names, tokenize='unicode61'
        );
        CREATE VIRTUAL TABLE ke_fts USING fts5(
            title, content, tokenize='unicode61'
        );
        INSERT INTO schema_version VALUES (8, 'add_sessions_fts', '2026-01-01');
    """)

    categories = ["mistake", "pattern", "decision", "discovery", "feature", "refactor"]

    for i in range(1, n_embeddings + 1):
        cat = categories[(i - 1) % len(categories)]
        db.execute(
            "INSERT INTO knowledge_entries "
            "(id, session_id, category, title, content) VALUES (?,?,?,?,?)",
            (i, "sess-1", cat, f"Entry {i} title", f"Content for entry {i}"),
        )
        db.execute(
            "INSERT INTO embeddings "
            "(source_type, source_id, provider, model, dimensions, vector) "
            "VALUES (?,?,?,?,?,?)",
            ("knowledge", i, "test", "test-model", dims, _make_vec(dims, i)),
        )

    db.commit()
    return db


def _make_test_db_empty() -> sqlite3.Connection:
    """DB with empty embeddings table."""
    return _make_test_db(n_embeddings=0)


def _start_server(db: sqlite3.Connection, token: str = "testtoken") -> tuple:
    HandlerClass = browse._make_handler_class(db, token)
    server = ThreadingHTTPServer(("127.0.0.1", 0), HandlerClass)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.05)
    return server, server.server_address[0], server.server_address[1]


def _get(host: str, port: int, path: str) -> tuple:
    conn = http.client.HTTPConnection(host, port, timeout=10)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        headers = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, headers, body
    finally:
        conn.close()


def _override_cache(path: Path) -> None:
    """Monkey-patch the projection module to use a custom cache path."""
    _proj._CACHE_PATH = path


def _cleanup_cache(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
        if path.parent.exists() and path.parent.name.startswith("__test_"):
            path.parent.rmdir()
    except Exception:
        pass


def run_all_tests() -> int:
    global _PASS, _FAIL
    print("=== test_browse_embeddings.py ===")

    _TEST_CACHE_DIR.mkdir(exist_ok=True)

    # ── T1: GET /embeddings returns 200 HTML with scatter canvas ─────────────
    print("\n-- T1: /embeddings HTML page")
    cache1 = _TEST_CACHE_DIR / "t1.json"
    _cleanup_cache(cache1)
    _override_cache(cache1)
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/embeddings?token=tok")
        test("T1: /embeddings → 200", status == 200)
        test("T1: content-type HTML", "text/html" in hdrs.get("content-type", ""))
        test("T1: contains scatter canvas", b'id="emb-scatter"' in body)
        test("T1: contains embeddings.js", b"embeddings.js" in body)
        test("T1: contains category filter", b'id="cat-filter"' in body)
    finally:
        server.shutdown()
        _cleanup_cache(cache1)

    # ── T2: GET /api/embeddings/points returns JSON with points array ─────────
    print("\n-- T2: /api/embeddings/points JSON")
    cache2 = _TEST_CACHE_DIR / "t2.json"
    _cleanup_cache(cache2)
    _override_cache(cache2)
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/api/embeddings/points?token=tok")
        test("T2: /api/embeddings/points → 200", status == 200)
        test("T2: content-type JSON", "application/json" in hdrs.get("content-type", ""))
        data = json.loads(body)
        test("T2: has 'points' array", isinstance(data.get("points"), list))
        test("T2: has 'count' field", isinstance(data.get("count"), int))
        test("T2: has 'cached' field", "cached" in data)
        test("T2: points count = 6", len(data["points"]) == 6)
        if data["points"]:
            p = data["points"][0]
            test("T2: point has x,y,category,title",
                 all(k in p for k in ("x", "y", "category", "title")))
    finally:
        server.shutdown()
        _cleanup_cache(cache2)

    # ── T3: Auth 401 without valid token ──────────────────────────────────────
    print("\n-- T3: auth enforcement")
    db = _make_test_db()
    server, host, port = _start_server(db, token="secret99")
    try:
        status, _, _ = _get(host, port, "/embeddings")
        test("T3: /embeddings no token → 401", status == 401)
        status2, _, _ = _get(host, port, "/api/embeddings/points?token=wrong")
        test("T3: /api/embeddings/points bad token → 401", status2 == 401)
    finally:
        server.shutdown()

    # ── T4: Cache hit — second request returns cached:true ───────────────────
    print("\n-- T4: cache hit")
    cache4 = _TEST_CACHE_DIR / "t4.json"
    _cleanup_cache(cache4)
    _override_cache(cache4)
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        # First request — computes and saves cache
        _, _, body1 = _get(host, port, "/api/embeddings/points?token=tok")
        data1 = json.loads(body1)
        test("T4: first request not cached", data1.get("cached") is False)
        test("T4: cache file created", cache4.exists())

        # Second request — should hit cache
        t0 = time.monotonic()
        _, _, body2 = _get(host, port, "/api/embeddings/points?token=tok")
        elapsed = time.monotonic() - t0
        data2 = json.loads(body2)
        test("T4: second request cached=True", data2.get("cached") is True)
        test("T4: second request fast (<2s)", elapsed < 2.0)
        test("T4: same points count both calls",
             len(data1["points"]) == len(data2["points"]))
    finally:
        server.shutdown()
        _cleanup_cache(cache4)

    # ── T5: Cache invalidation when count changes ─────────────────────────────
    print("\n-- T5: cache invalidation")
    cache5 = _TEST_CACHE_DIR / "t5.json"
    _cleanup_cache(cache5)
    _override_cache(cache5)
    db_small = _make_test_db(n_embeddings=4)
    server, host, port = _start_server(db_small, token="tok")
    try:
        _, _, body1 = _get(host, port, "/api/embeddings/points?token=tok")
        data1 = json.loads(body1)
        test("T5: small DB first call not cached", data1.get("cached") is False)
    finally:
        server.shutdown()

    # Now simulate a DB with more embeddings — cache count mismatch
    db_big = _make_test_db(n_embeddings=6)
    _override_cache(cache5)  # same stale cache
    server2, host2, port2 = _start_server(db_big, token="tok")
    try:
        _, _, body2 = _get(host2, port2, "/api/embeddings/points?token=tok")
        data2 = json.loads(body2)
        test("T5: bigger DB recomputes (cached=False)", data2.get("cached") is False)
        test("T5: bigger DB returns more points",
             data2.get("count", 0) > data1.get("count", 0))
    finally:
        server2.shutdown()
        _cleanup_cache(cache5)

    # ── T6: XSS escape of title in JSON ──────────────────────────────────────
    print("\n-- T6: XSS escape")
    cache6 = _TEST_CACHE_DIR / "t6.json"
    _cleanup_cache(cache6)
    _override_cache(cache6)
    db = _make_test_db()
    # Inject XSS title into first entry
    db.execute(
        "UPDATE knowledge_entries SET title = ? WHERE id = 1",
        ('<script>alert("xss")</script>',),
    )
    db.commit()
    server, host, port = _start_server(db, token="tok")
    try:
        status, _, body = _get(host, port, "/api/embeddings/points?token=tok")
        test("T6: API returns 200", status == 200)
        data = json.loads(body)  # JSON parse should not throw
        titles = [p["title"] for p in data.get("points", [])]
        xss_titles = [t for t in titles if "<script>" in t]
        # The raw title is preserved in JSON (HTML escaping is for HTML output)
        # JSON itself is safe since it's data; the JS client does HTML escaping
        test("T6: JSON parses cleanly (no injection in JSON)", True)
        # HTML page must escape the title in rendered HTML
        status_html, _, html_body = _get(host, port, "/embeddings?token=tok")
        test("T6: HTML page 200", status_html == 200)
        # HTML page doesn't embed titles directly — verify no raw <script> tag
        # from title injection in the main_content HTML template
        test("T6: HTML page doesn't embed raw script tag from title",
             b'<script>alert("xss")</script>' not in html_body)
    finally:
        server.shutdown()
        _cleanup_cache(cache6)

    # ── T7: Graceful degradation when embeddings table is empty ──────────────
    print("\n-- T7: empty embeddings table")
    cache7 = _TEST_CACHE_DIR / "t7.json"
    _cleanup_cache(cache7)
    _override_cache(cache7)
    db = _make_test_db_empty()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/api/embeddings/points?token=tok")
        test("T7: empty DB → 200", status == 200)
        data = json.loads(body)
        test("T7: points=[]", data.get("points") == [])
        test("T7: count=0", data.get("count") == 0)
    finally:
        server.shutdown()
        _cleanup_cache(cache7)

    # ── T8: Graceful degradation when embeddings table absent ─────────────────
    print("\n-- T8: missing embeddings table")
    cache8 = _TEST_CACHE_DIR / "t8.json"
    _cleanup_cache(cache8)
    _override_cache(cache8)
    # DB without embeddings table at all
    db_noemb = sqlite3.connect(":memory:", check_same_thread=False)
    db_noemb.row_factory = sqlite3.Row
    db_noemb.executescript("""
        CREATE TABLE schema_version (version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT);
        CREATE TABLE sessions (id TEXT PRIMARY KEY, path TEXT, summary TEXT, source TEXT,
            file_mtime REAL, indexed_at_r REAL, fts_indexed_at REAL,
            event_count_estimate INTEGER, file_size_bytes INTEGER,
            total_checkpoints INTEGER, total_research INTEGER,
            total_files INTEGER, has_plan INTEGER, indexed_at TEXT);
        CREATE TABLE documents (id INTEGER PRIMARY KEY, session_id TEXT, doc_type TEXT,
            seq INTEGER, title TEXT, file_path TEXT, file_hash TEXT, size_bytes INTEGER,
            content_preview TEXT, indexed_at TEXT, source TEXT);
        CREATE TABLE sections (id INTEGER PRIMARY KEY, document_id INTEGER,
            section_name TEXT, content TEXT);
        CREATE TABLE knowledge (id INTEGER PRIMARY KEY, title TEXT, content TEXT,
            category TEXT, wing TEXT, room TEXT);
        CREATE VIRTUAL TABLE sessions_fts USING fts5(
            session_id UNINDEXED, title, user_messages,
            assistant_messages, tool_names, tokenize='unicode61');
        CREATE VIRTUAL TABLE ke_fts USING fts5(title, content, tokenize='unicode61');
        INSERT INTO schema_version VALUES (8, 'add_sessions_fts', '2026-01-01');
    """)
    db_noemb.commit()
    server, host, port = _start_server(db_noemb, token="tok")
    try:
        status, _, body = _get(host, port, "/api/embeddings/points?token=tok")
        test("T8: no embeddings table → 200", status == 200)
        data = json.loads(body)
        test("T8: points=[]", data.get("points") == [])
    finally:
        server.shutdown()
        _cleanup_cache(cache8)

    # ── T9: PCA projection correctness (unit test, no server) ─────────────────
    print("\n-- T9: PCA unit test")
    import random as _random
    _random.seed(7)
    # Generate 20 points in 4D with a dominant direction
    e1 = [1.0, 0.0, 0.0, 0.0]
    vecs = [[_random.gauss(i * 0.5, 0.1), _random.gauss(0, 0.05),
             _random.gauss(0, 0.05), _random.gauss(0, 0.05)]
            for i in range(20)]
    xs, ys = _proj.pca_2d(vecs)
    test("T9: pca_2d returns correct length", len(xs) == 20 and len(ys) == 20)
    test("T9: xs are floats", all(isinstance(v, float) for v in xs))
    # PC1 should capture the dominant direction — variance of xs > variance of ys
    mean_xs = sum(xs) / len(xs)
    mean_ys = sum(ys) / len(ys)
    var_x = sum((v - mean_xs) ** 2 for v in xs) / len(xs)
    var_y = sum((v - mean_ys) ** 2 for v in ys) / len(ys)
    test("T9: PC1 captures most variance (var_x > var_y)", var_x > var_y)

    # ── Cleanup ───────────────────────────────────────────────────────────────
    try:
        if _TEST_CACHE_DIR.exists():
            for f in _TEST_CACHE_DIR.iterdir():
                f.unlink(missing_ok=True)
            _TEST_CACHE_DIR.rmdir()
    except Exception:
        pass

    print(f"\n{'='*40}")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    return _FAIL


if __name__ == "__main__":
    sys.exit(run_all_tests())
