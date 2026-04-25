#!/usr/bin/env python3
"""
test_browse_graph.py — Tests for the F1 Knowledge Graph feature.

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
import browse.core.similarity as _sim  # noqa: E402

_PASS = 0
_FAIL = 0

_TEST_CACHE_DIR = Path(__file__).parent / "__test_cache_graph__"


def test(name: str, expr: bool) -> None:
    global _PASS, _FAIL
    if expr:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}")


def _make_test_db() -> sqlite3.Connection:
    """Create an in-memory SQLite DB with the required schema, including knowledge tables."""
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
            first_seen TEXT,
            last_seen TEXT,
            source TEXT DEFAULT 'copilot',
            topic_key TEXT,
            revision_count INTEGER DEFAULT 1,
            content_hash TEXT,
            wing TEXT DEFAULT '',
            room TEXT DEFAULT '',
            facts TEXT DEFAULT '[]',
            est_tokens INTEGER DEFAULT 0,
            task_id TEXT DEFAULT '',
            affected_files TEXT DEFAULT '[]'
        );
        CREATE TABLE entity_relations (
            id INTEGER PRIMARY KEY,
            subject TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object TEXT NOT NULL,
            noted_at TEXT DEFAULT (datetime('now')),
            session_id TEXT DEFAULT ''
        );
        CREATE TABLE knowledge_relations (
            id INTEGER PRIMARY KEY,
            source_id INTEGER,
            target_id INTEGER,
            relation_type TEXT NOT NULL,
            confidence REAL DEFAULT 0.8,
            created_at TEXT
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
        INSERT INTO schema_version VALUES (8, 'add_sessions_fts', '2026-01-01');
    """)

    # Insert sample knowledge entries
    db.executemany(
        "INSERT INTO knowledge_entries (session_id, document_id, category, title, content, wing, room) VALUES (?,?,?,?,?,?,?)",
        [
            ("sess-1", 1, "mistake", "Fix null pointer error", "Always check for null", "backend", "auth"),
            ("sess-1", 1, "pattern", "Use parameterized SQL", "Never interpolate SQL", "backend", "db"),
            ("sess-1", 1, "decision", "Chose SQLite over Postgres", "Simpler for local tool", "backend", "db"),
            ("sess-2", 2, "discovery", "FTS5 supports NEAR queries", "Use NEAR/n", "backend", "fts"),
            ("sess-2", 2, "feature",  "Dark mode toggle", "Uses prefers-color-scheme", "frontend", "ui"),
        ],
    )

    # Insert sample entity relations
    db.executemany(
        "INSERT INTO entity_relations (subject, predicate, object) VALUES (?,?,?)",
        [
            ("Fix null pointer error", "related_to", "Use parameterized SQL"),
            ("Use parameterized SQL", "implements", "parameterized_queries"),
        ],
    )
    db.executemany(
        "INSERT INTO knowledge_relations (source_id, target_id, relation_type, confidence) VALUES (?,?,?,?)",
        [
            (1, 2, "RESOLVED_BY", 0.88),
            (2, 4, "TAG_OVERLAP", 0.55),
            (4, 5, "SAME_SESSION", 0.70),
        ],
    )
    db.executemany(
        "INSERT INTO embeddings (source_type, source_id, provider, model, dimensions, vector) VALUES (?,?,?,?,?,?)",
        [
            ("knowledge", 1, "test", "test-model", 3, _pack_vec([1.0, 0.0, 0.0])),
            ("knowledge", 2, "test", "test-model", 3, _pack_vec([0.95, 0.05, 0.0])),
            ("knowledge", 3, "test", "test-model", 3, _pack_vec([0.0, 1.0, 0.0])),
            ("knowledge", 4, "test", "test-model", 3, _pack_vec([0.8, 0.2, 0.0])),
            ("knowledge", 5, "test", "test-model", 3, _pack_vec([0.0, 0.0, 1.0])),
        ],
    )

    db.commit()
    return db


def _pack_vec(values: list[float]) -> bytes:
    return struct.pack(f"<{len(values)}f", *values)


def _override_similarity_cache(path: Path) -> None:
    _sim._CACHE_PATH = path


def _cleanup_cache(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
        if path.parent.exists() and path.parent.name.startswith("__test_"):
            path.parent.rmdir()
    except Exception:
        pass


def _set_similarity_vector(db: sqlite3.Connection, source_id: int, values: list[float]) -> None:
    db.execute(
        "UPDATE embeddings SET vector = ? WHERE source_type = 'knowledge' AND source_id = ?",
        (_pack_vec(values), source_id),
    )
    db.commit()


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _start_server(db: sqlite3.Connection, token: str = "testtoken") -> tuple:
    """Start a test server; return (server, host, port)."""
    HandlerClass = browse._make_handler_class(db, token)
    server = ThreadingHTTPServer(("127.0.0.1", 0), HandlerClass)
    host, port = server.server_address
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.05)
    return server, host, port


def _get(host: str, port: int, path: str) -> tuple:
    """Perform a GET request; return (status, headers_dict, body)."""
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        headers = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, headers, body
    finally:
        conn.close()


def run_all_tests() -> int:
    print("=== test_browse_graph.py ===")
    _TEST_CACHE_DIR.mkdir(exist_ok=True)

    # ── T1: GET /graph returns 200 HTML with canvas div and cytoscape script ──
    print("\n-- T1: /graph HTML page")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/graph?token=tok")
        test("T1: /graph → 200", status == 200)
        test("T1: content-type HTML", "text/html" in hdrs.get("content-type", ""))
        test("T1: contains graph-canvas div", b'id="graph-canvas"' in body)
        test("T1: contains cytoscape script tag", b"cytoscape.min.js" in body)
    finally:
        server.shutdown()

    # ── T2: GET /api/graph returns JSON with nodes and edges arrays ────────────
    print("\n-- T2: /api/graph JSON response")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/api/graph?token=tok")
        test("T2: /api/graph → 200", status == 200)
        test("T2: content-type JSON", "application/json" in hdrs.get("content-type", ""))
        data = json.loads(body)
        test("T2: has 'nodes' array", isinstance(data.get("nodes"), list))
        test("T2: has 'edges' array", isinstance(data.get("edges"), list))
        test("T2: 'truncated' key present", "truncated" in data)
        test("T2: nodes count ≥ 5", len(data["nodes"]) >= 5)
    finally:
        server.shutdown()

    # ── T3: /api/graph?limit=2 caps node count ────────────────────────────────
    print("\n-- T3: limit param")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/api/graph?token=tok&limit=2")
        test("T3: /api/graph?limit=2 → 200", status == 200)
        data = json.loads(body)
        entry_nodes = [n for n in data["nodes"] if n.get("kind") == "entry"]
        test("T3: entry nodes capped at 2", len(entry_nodes) <= 2)
        test("T3: truncated=true when limit hit", data.get("truncated") is True)
    finally:
        server.shutdown()

    # ── T4: /api/graph?format=json returns JSON content-type ──────────────────
    print("\n-- T4: format=json")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/api/graph?token=tok&format=json")
        test("T4: /api/graph?format=json → 200", status == 200)
        test("T4: content-type is application/json", "application/json" in hdrs.get("content-type", ""))
        data = json.loads(body)
        test("T4: valid JSON with nodes", isinstance(data.get("nodes"), list))
    finally:
        server.shutdown()

    # ── T5: Graph route respects token auth (401 without token) ───────────────
    print("\n-- T5: auth required")
    db = _make_test_db()
    server, host, port = _start_server(db, token="secret")
    try:
        status_html, _, _ = _get(host, port, "/graph")
        test("T5: /graph without token → 401", status_html == 401)
        status_api, _, _ = _get(host, port, "/api/graph")
        test("T5: /api/graph without token → 401", status_api == 401)
        status_sim, _, _ = _get(host, port, "/api/graph/similarity?entry_id=1")
        test("T5: /api/graph/similarity without token → 401", status_sim == 401)
        status_com, _, _ = _get(host, port, "/api/graph/communities")
        test("T5: /api/graph/communities without token → 401", status_com == 401)
        status_bad, _, _ = _get(host, port, "/graph?token=wrong")
        test("T5: /graph with wrong token → 401", status_bad == 401)
    finally:
        server.shutdown()

    # ── T6: Node shape validation ──────────────────────────────────────────────
    print("\n-- T6: node shape")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, _, body = _get(host, port, "/api/graph?token=tok")
        data = json.loads(body)
        entry_nodes = [n for n in data["nodes"] if n.get("kind") == "entry"]
        test("T6: entry node has id starting with 'e-'",
             all(n["id"].startswith("e-") for n in entry_nodes))
        test("T6: entry node has color field",
             all("color" in n for n in entry_nodes))
        test("T6: entry node has category field",
             all("category" in n for n in entry_nodes))
    finally:
        server.shutdown()

    # ── T7: Edge shape from entity_relations ──────────────────────────────────
    print("\n-- T7: edges from entity_relations")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, _, body = _get(host, port, "/api/graph?token=tok")
        data = json.loads(body)
        edges = data.get("edges", [])
        test("T7: edges list present", isinstance(edges, list))
        if edges:
            test("T7: edge has source+target+relation",
                 all("source" in e and "target" in e and "relation" in e for e in edges))
        else:
            test("T7: edges list is valid (may be empty)", True)
    finally:
        server.shutdown()

    # ── T8: /api/graph/evidence uses knowledge_relations with truthful meta ───
    print("\n-- T8: /api/graph/evidence JSON response")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(host, port, "/api/graph/evidence?token=tok")
        test("T8: /api/graph/evidence → 200", status == 200)
        test("T8: content-type JSON", "application/json" in hdrs.get("content-type", ""))
        data = json.loads(body)
        test("T8: has meta.edge_source=knowledge_relations",
             data.get("meta", {}).get("edge_source") == "knowledge_relations")
        test("T8: evidence edges include relation_type+confidence",
             all("relation_type" in e and "confidence" in e for e in data.get("edges", [])))
        test("T8: evidence nodes are entry-kind only",
             all(n.get("kind") == "entry" for n in data.get("nodes", [])))
    finally:
        server.shutdown()

    # ── T9: relation_type filtering returns only requested types ───────────────
    print("\n-- T9: evidence relation_type filter")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, _, body = _get(
            host, port, "/api/graph/evidence?token=tok&relation_type=TAG_OVERLAP"
        )
        data = json.loads(body)
        edges = data.get("edges", [])
        test("T9: endpoint returns 200", status == 200)
        test("T9: filtered edge list is non-empty", len(edges) >= 1)
        test("T9: all filtered edges are TAG_OVERLAP",
             all(e.get("relation_type") == "TAG_OVERLAP" for e in edges))
        test("T9: confidence value preserved from DB",
             any(abs(float(e.get("confidence", 0)) - 0.55) < 1e-9 for e in edges))
        test("T9: meta.relation_types reflects filtered semantics",
             data.get("meta", {}).get("relation_types") == ["TAG_OVERLAP"])
    finally:
        server.shutdown()

    # ── T10: evidence edges join to real entry nodes via IDs ───────────────────
    print("\n-- T10: evidence join integrity")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, _, body = _get(host, port, "/api/graph/evidence?token=tok")
        data = json.loads(body)
        node_ids = {n.get("id") for n in data.get("nodes", [])}
        edges = data.get("edges", [])
        test("T10: endpoint returns 200", status == 200)
        test("T10: all evidence edges resolve source+target node IDs",
             all(e.get("source") in node_ids and e.get("target") in node_ids for e in edges))
    finally:
        server.shutdown()

    # ── T11: legacy /api/graph remains backward-compatible ─────────────────────
    print("\n-- T11: legacy compatibility")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        legacy_status, _, legacy_body = _get(host, port, "/api/graph?token=tok")
        ev_status, _, ev_body = _get(host, port, "/api/graph/evidence?token=tok")
        legacy = json.loads(legacy_body)
        evidence = json.loads(ev_body)
        legacy_kinds = {n.get("kind") for n in legacy.get("nodes", [])}
        test("T11: legacy endpoint still returns 200", legacy_status == 200)
        test("T11: evidence endpoint returns 200", ev_status == 200)
        test("T11: legacy edges keep relation field",
             all("relation" in e for e in legacy.get("edges", [])))
        test("T11: legacy payload unchanged (no evidence meta)",
             "meta" not in legacy)
        test("T11: evidence payload uses relation_type field",
             all("relation_type" in e for e in evidence.get("edges", [])))
        test("T11: semantic difference visible (legacy may include entity nodes)",
             "entity" in legacy_kinds and all(n.get("kind") == "entry" for n in evidence.get("nodes", [])))
    finally:
        server.shutdown()

    # ── T12: /api/graph/similarity returns deterministic top-k neighbors ───────
    print("\n-- T12: /api/graph/similarity top-k")
    cache12 = _TEST_CACHE_DIR / "t12_similarity.json"
    _cleanup_cache(cache12)
    _override_similarity_cache(cache12)
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, hdrs, body = _get(
            host,
            port,
            "/api/graph/similarity?token=tok&entry_id=1&entry_id=4&k=2",
        )
        data = json.loads(body)
        test("T12: endpoint returns 200", status == 200)
        test("T12: content-type JSON", "application/json" in hdrs.get("content-type", ""))
        test("T12: response has results array", isinstance(data.get("results"), list))
        test("T12: response has meta.method=cosine_knn",
             data.get("meta", {}).get("method") == "cosine_knn")
        res = data.get("results", [])
        by_id = {row.get("entry_id"): row for row in res}
        n1 = by_id.get(1, {}).get("neighbors", [])
        test("T12: entry 1 has two neighbors", len(n1) == 2)
        if len(n1) >= 2:
            test("T12: deterministic ordering by score", n1[0].get("id") == 2 and n1[1].get("id") == 4)
            test("T12: scores are descending", float(n1[0].get("score", 0)) >= float(n1[1].get("score", 0)))
    finally:
        server.shutdown()
        _cleanup_cache(cache12)

    # ── T13: similarity cache invalidates on fingerprint change (same count) ───
    print("\n-- T13: similarity invalidation")
    cache13 = _TEST_CACHE_DIR / "t13_similarity.json"
    _cleanup_cache(cache13)
    _override_similarity_cache(cache13)
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status1, _, body1 = _get(host, port, "/api/graph/similarity?token=tok&entry_id=1&k=1")
        data1 = json.loads(body1)
        first_neighbor = data1.get("results", [{}])[0].get("neighbors", [{}])[0].get("id")
        test("T13: first call succeeds", status1 == 200)
        test("T13: first call cached=false", data1.get("meta", {}).get("cached") is False)
    finally:
        server.shutdown()

    # Mutate vector content while preserving row count and IDs.
    _set_similarity_vector(db, 2, [0.1, 0.9, 0.0])
    server2, host2, port2 = _start_server(db, token="tok")
    try:
        status2, _, body2 = _get(host2, port2, "/api/graph/similarity?token=tok&entry_id=1&k=1")
        data2 = json.loads(body2)
        second_neighbor = data2.get("results", [{}])[0].get("neighbors", [{}])[0].get("id")
        test("T13: second call succeeds", status2 == 200)
        test("T13: changed data triggers recompute cached=false",
             data2.get("meta", {}).get("cached") is False)
        test("T13: top neighbor changes after vector mutation",
             first_neighbor != second_neighbor and second_neighbor == 4)
    finally:
        server2.shutdown()
        _cleanup_cache(cache13)

    # ── T14: similarity degrades gracefully when embeddings missing/empty ──────
    print("\n-- T14: similarity graceful degradation")
    cache14 = _TEST_CACHE_DIR / "t14_similarity.json"
    _cleanup_cache(cache14)
    _override_similarity_cache(cache14)
    db_empty = _make_test_db()
    db_empty.execute("DELETE FROM embeddings")
    db_empty.commit()
    server, host, port = _start_server(db_empty, token="tok")
    try:
        status, _, body = _get(host, port, "/api/graph/similarity?token=tok&entry_id=1&entry_id=2&k=3")
        data = json.loads(body)
        test("T14: empty embeddings returns 200", status == 200)
        test("T14: empty embeddings produce empty neighbors",
             all(row.get("neighbors") == [] for row in data.get("results", [])))
        test("T14: empty embeddings report count=0", data.get("meta", {}).get("embedding_count") == 0)
    finally:
        server.shutdown()

    db_missing = sqlite3.connect(":memory:", check_same_thread=False)
    db_missing.row_factory = sqlite3.Row
    db_missing.executescript("""
        CREATE TABLE schema_version (version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT);
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
        CREATE VIRTUAL TABLE sessions_fts USING fts5(
            session_id UNINDEXED, title, user_messages,
            assistant_messages, tool_names, tokenize='unicode61'
        );
        INSERT INTO schema_version VALUES (8, 'add_sessions_fts', '2026-01-01');
    """)
    db_missing.commit()
    server2, host2, port2 = _start_server(db_missing, token="tok")
    try:
        status, _, body = _get(host2, port2, "/api/graph/similarity?token=tok&entry_id=1")
        data = json.loads(body)
        test("T14: missing embeddings table returns 200", status == 200)
        test("T14: missing embeddings returns results array",
             isinstance(data.get("results"), list))
    finally:
        server2.shutdown()
        _cleanup_cache(cache14)

    # ── T15: similarity cache stores bounded per-entry top-K only ──────────────
    print("\n-- T15: similarity cache bounded per-entry")
    cache15 = _TEST_CACHE_DIR / "t15_similarity.json"
    _cleanup_cache(cache15)
    _override_similarity_cache(cache15)
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, _, body = _get(host, port, "/api/graph/similarity?token=tok&entry_id=1&k=2")
        data = json.loads(body)
        cache_data = _read_json(cache15)
        neighbor_map = cache_data.get("neighbors", {})
        test("T15: endpoint returns 200", status == 200)
        test("T15: cache only stores requested source entry", set(neighbor_map.keys()) == {"1"})
        test("T15: cached neighbors bounded by max_cached_k",
             len(neighbor_map.get("1", [])) <= int(data.get("meta", {}).get("max_cached_k", 0)))
    finally:
        server.shutdown()
        _cleanup_cache(cache15)

    # ── T16: cold compute guard degrades safely under strict pair budget ───────
    print("\n-- T16: cold compute guard")
    cache16 = _TEST_CACHE_DIR / "t16_similarity.json"
    _cleanup_cache(cache16)
    _override_similarity_cache(cache16)
    old_pair_budget = _sim._MAX_COMPUTE_PAIRS
    _sim._MAX_COMPUTE_PAIRS = 4
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status, _, body = _get(
            host, port, "/api/graph/similarity?token=tok&entry_id=1&entry_id=2&k=2"
        )
        data = json.loads(body)
        by_id = {row.get("entry_id"): row.get("neighbors", []) for row in data.get("results", [])}
        test("T16: endpoint returns 200", status == 200)
        test("T16: compute guard reports degraded", data.get("meta", {}).get("degraded") is True)
        test("T16: one requested source is skipped under budget",
             by_id.get(1) != [] and by_id.get(2) == [])
        test("T16: skipped entry ids are reported",
             data.get("meta", {}).get("skipped_entry_ids") == [2])
    finally:
        _sim._MAX_COMPUTE_PAIRS = old_pair_budget
        server.shutdown()
        _cleanup_cache(cache16)

    # ── T17: /api/graph/communities is deterministic and grounded ───────────────
    print("\n-- T17: /api/graph/communities deterministic summaries")
    db = _make_test_db()
    server, host, port = _start_server(db, token="tok")
    try:
        status1, hdrs1, body1 = _get(host, port, "/api/graph/communities?token=tok")
        status2, _, body2 = _get(host, port, "/api/graph/communities?token=tok")
        data1 = json.loads(body1)
        data2 = json.loads(body2)
        communities = data1.get("communities", [])
        primary = communities[0] if communities else {}
        test("T17: endpoint returns 200", status1 == 200 and status2 == 200)
        test("T17: content-type JSON", "application/json" in hdrs1.get("content-type", ""))
        test("T17: response stable across repeated calls", data1 == data2)
        test("T17: returns at least one useful community", len(communities) >= 1)
        test("T17: deterministic community id", primary.get("id") == "c-1")
        test("T17: grounded entry_count from member entries", primary.get("entry_count") == 4)
        test("T17: top wings derived from member data",
             primary.get("wings") == ["backend", "frontend"])
        test("T17: representative entries are deterministic and concrete",
             [entry.get("id") for entry in primary.get("representative_entries", [])] == [2, 4, 1])
    finally:
        server.shutdown()

    # ── T18: communities suppress singleton-only noise ──────────────────────────
    print("\n-- T18: communities singleton suppression")
    db = _make_test_db()
    db.execute("DELETE FROM knowledge_relations")
    db.execute(
        "INSERT INTO knowledge_relations (source_id, target_id, relation_type, confidence) VALUES (?,?,?,?)",
        (3, 3, "SAME_SESSION", 0.7),
    )
    db.commit()
    server, host, port = _start_server(db, token="tok")
    try:
        status, _, body = _get(host, port, "/api/graph/communities?token=tok")
        data = json.loads(body)
        test("T18: endpoint returns 200", status == 200)
        test("T18: singleton-only graph returns no useful communities",
             data.get("communities") == [])
    finally:
        server.shutdown()

    # ── Cleanup ───────────────────────────────────────────────────────────────
    try:
        if _TEST_CACHE_DIR.exists():
            for f in _TEST_CACHE_DIR.iterdir():
                f.unlink(missing_ok=True)
            _TEST_CACHE_DIR.rmdir()
    except Exception:
        pass

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*40}")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    return _FAIL


if __name__ == "__main__":
    sys.exit(run_all_tests())
