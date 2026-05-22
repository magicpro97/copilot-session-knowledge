#!/usr/bin/env python3
"""test_browse_api_contract.py — Golden-fixture API contract tests for the browse backend.

Purpose
-------
Captures the API contract of the current Python browse backend as golden JSON
fixtures so future Rust (or other) backend ports can be validated against the
same stable contracts.

Each fixture in tests/fixtures/api/ defines:
  - The expected HTTP status code
  - The expected Content-Type prefix
  - A ``body_shape`` dict where each value is either:
      - An exact match (string, int, bool, null, list, dict)
      - A type-sentinel string: ``__str__``, ``__int__``, ``__float__``,
        ``__bool__``, ``__list__``, ``__dict__``, ``__any__``,
        ``__null_or_str__``, ``__null_or_int__``, ``__null_or_dict__``,
        ``__null_or_list__``, ``__null_or_float__``
      - A nested dict (recursive shape check)
      - A single-element list ``[shape]`` meaning each list item matches
        ``shape``

Usage
-----
Run against the built-in Python backend (default; CI-safe):
    python tests/test_browse_api_contract.py

Run against an external backend URL (future Rust port, staging, etc.):
    BROWSE_BASE_URL=http://127.0.0.1:8080 BROWSE_TOKEN=tok \\
        python tests/test_browse_api_contract.py

Regenerate / update golden fixtures from the live Python server:
    python tests/test_browse_api_contract.py --update

When ``BROWSE_BASE_URL`` is set, only a hard-coded subset of mock-free tests
is run against the external backend; tests that require Python mocks are
skipped.
"""

import http.client
import json
import os
import sqlite3
import sys
import threading
import time
import unittest.mock
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
sys.path.insert(0, str(_REPO))

import browse  # noqa: E402

FIXTURES_DIR = _HERE / "fixtures" / "api"

_PASS = 0
_FAIL = 0


# ── Test reporting helpers ─────────────────────────────────────────────────────


def _record(name: str, ok: bool, diffs: list[str] | None = None) -> None:
    global _PASS, _FAIL
    if ok:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}")
        for diff in diffs or []:
            print(f"         ↳ {diff}")


# ── In-process test server (Python backend) ────────────────────────────────────


def _make_test_db() -> sqlite3.Connection:
    """Create a seeded in-memory SQLite DB identical to test_browse_api.py."""
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
        CREATE TABLE knowledge_entries (
            id INTEGER PRIMARY KEY, title TEXT, content TEXT,
            category TEXT, wing TEXT, room TEXT,
            entry_type TEXT, session_id TEXT, created_at TEXT
        );
        CREATE TABLE entity_relations (
            id INTEGER PRIMARY KEY, source TEXT, target TEXT, relation TEXT
        );
        CREATE TABLE embeddings (
            id INTEGER PRIMARY KEY, entry_id INTEGER, vector BLOB
        );
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT
        );
        CREATE TABLE sync_state (
            key TEXT PRIMARY KEY, value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE sync_txns (
            txn_id TEXT PRIMARY KEY, replica_id TEXT NOT NULL, status TEXT NOT NULL,
            created_at TEXT NOT NULL, committed_at TEXT DEFAULT ''
        );
        CREATE TABLE sync_ops (
            id INTEGER PRIMARY KEY AUTOINCREMENT, txn_id TEXT NOT NULL,
            table_name TEXT NOT NULL, op_type TEXT NOT NULL, row_stable_id TEXT NOT NULL,
            row_payload TEXT NOT NULL, op_index INTEGER NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE sync_cursors (
            replica_id TEXT PRIMARY KEY, last_txn_id TEXT DEFAULT '',
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE sync_failures (
            id INTEGER PRIMARY KEY AUTOINCREMENT, txn_id TEXT DEFAULT '',
            table_name TEXT DEFAULT '', row_stable_id TEXT DEFAULT '',
            error_code TEXT DEFAULT '', error_message TEXT DEFAULT '',
            failed_at TEXT NOT NULL, retry_count INTEGER DEFAULT 0
        );
        INSERT INTO schema_version VALUES (9, 'add_search_feedback', '2026-01-01');
    """)
    db.execute("CREATE VIRTUAL TABLE ke_fts USING fts5(title, content, tokenize='unicode61')")
    db.execute(
        """CREATE VIRTUAL TABLE sessions_fts USING fts5(
            session_id UNINDEXED, title, user_messages,
            assistant_messages, tool_names, tokenize='unicode61'
        )"""
    )
    for i in range(3):
        sid = f"session-id-{i:04d}-abcdef"
        db.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                sid,
                f"/path/to/session{i}",
                f"Test session {i}",
                "copilot",
                float(i),
                float(i) + 1,
                float(i) + 2,
                10 + i,
                1024,
                1,
                0,
                3,
                0,
                f"2026-01-0{i + 1}",
            ),
        )
        db.execute(
            "INSERT INTO sessions_fts VALUES (?,?,?,?,?)",
            (sid, f"Test session {i}", f"user message {i}", f"assistant reply {i}", "bash"),
        )
    sid0 = "session-id-0000-abcdef"
    db.execute(
        "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (1, sid0, "checkpoint", 1, "Checkpoint 1", "/path", "abc", 100, "preview", "2026-01-01", "copilot"),
    )
    db.execute("INSERT INTO sections VALUES (?,?,?,?)", (1, 1, "overview", "Session overview content"))
    db.execute("INSERT OR REPLACE INTO sync_state(key, value) VALUES(?, ?)", ("local_replica_id", "local"))
    db.execute(
        "INSERT INTO sync_txns VALUES (?,?,?,?,?)",
        ("txn-pending-1", "local", "pending", "2026-01-02T00:00:00Z", ""),
    )
    db.execute(
        "INSERT INTO sync_txns VALUES (?,?,?,?,?)",
        ("txn-committed-1", "local", "committed", "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z"),
    )
    db.execute(
        "INSERT INTO sync_ops(txn_id, table_name, op_type, row_stable_id, row_payload, op_index, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        ("txn-pending-1", "knowledge_entries", "upsert", "k1", "{}", 0, "2026-01-02T00:00:00Z"),
    )
    db.execute(
        "INSERT INTO sync_cursors VALUES (?,?,?)",
        ("gateway", "txn-committed-1", "2026-01-01T00:02:00Z"),
    )
    db.execute(
        "INSERT INTO sync_failures(txn_id, table_name, row_stable_id, error_code, error_message, failed_at, retry_count)"
        " VALUES (?,?,?,?,?,?,?)",
        (
            "txn-pending-1",
            "knowledge_entries",
            "k1",
            "network_timeout",
            "timeout while contacting reference gateway",
            "2026-01-02T00:03:00Z",
            1,
        ),
    )
    db.commit()
    return db


def _make_empty_db() -> sqlite3.Connection:
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
        CREATE TABLE documents (id INTEGER PRIMARY KEY, session_id TEXT, doc_type TEXT,
            seq INTEGER, title TEXT, file_path TEXT, file_hash TEXT, size_bytes INTEGER,
            content_preview TEXT, indexed_at TEXT, source TEXT);
        CREATE TABLE sections (id INTEGER PRIMARY KEY, document_id INTEGER,
            section_name TEXT, content TEXT);
        CREATE TABLE knowledge_entries (id INTEGER PRIMARY KEY, title TEXT, content TEXT,
            category TEXT, wing TEXT, room TEXT, entry_type TEXT, session_id TEXT, created_at TEXT);
        CREATE TABLE entity_relations (id INTEGER PRIMARY KEY, source TEXT, target TEXT, relation TEXT);
        CREATE TABLE embeddings (id INTEGER PRIMARY KEY, entry_id INTEGER, vector BLOB);
        CREATE TABLE schema_version (version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT);
        CREATE TABLE sync_state (key TEXT PRIMARY KEY, value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now')));
        CREATE TABLE sync_txns (txn_id TEXT PRIMARY KEY, replica_id TEXT NOT NULL,
            status TEXT NOT NULL, created_at TEXT NOT NULL, committed_at TEXT DEFAULT '');
        CREATE TABLE sync_ops (id INTEGER PRIMARY KEY AUTOINCREMENT, txn_id TEXT NOT NULL,
            table_name TEXT NOT NULL, op_type TEXT NOT NULL, row_stable_id TEXT NOT NULL,
            row_payload TEXT NOT NULL, op_index INTEGER NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE sync_cursors (replica_id TEXT PRIMARY KEY, last_txn_id TEXT DEFAULT '',
            updated_at TEXT DEFAULT (datetime('now')));
        CREATE TABLE sync_failures (id INTEGER PRIMARY KEY AUTOINCREMENT, txn_id TEXT DEFAULT '',
            table_name TEXT DEFAULT '', row_stable_id TEXT DEFAULT '',
            error_code TEXT DEFAULT '', error_message TEXT DEFAULT '',
            failed_at TEXT NOT NULL, retry_count INTEGER DEFAULT 0);
        INSERT INTO schema_version VALUES (9, 'add_search_feedback', '2026-01-01');
    """)
    db.commit()
    return db


def _start_server(db: sqlite3.Connection, token: str = "tok") -> tuple:
    handler = browse._make_handler_class(db, token)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    host, port = server.server_address
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(0.05)
    return server, host, port


# ── HTTP helpers ───────────────────────────────────────────────────────────────

# Set to True by run_tests() when an https:// external URL is supplied so that
# _request() can select HTTPSConnection without requiring signature changes on
# every test_* function.
_USE_SSL: bool = False


def _request(
    method: str,
    host: str,
    port: int,
    path: str,
    token: str = "tok",
    body: dict | None = None,
) -> tuple[int, dict, object]:
    sep = "&" if "?" in path else "?"
    full_path = f"{path}{sep}token={urllib.parse.quote(token)}"
    conn = (http.client.HTTPSConnection if _USE_SSL else http.client.HTTPConnection)(host, port, timeout=5)
    try:
        if method in ("POST", "PATCH", "DELETE") and body is not None:
            encoded = json.dumps(body).encode("utf-8")
            conn.request(method, full_path, body=encoded, headers={"Content-Type": "application/json"})
        else:
            conn.request(method, full_path)
        resp = conn.getresponse()
        raw = resp.read()
        headers = {k.lower(): v for k, v in resp.getheaders()}
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            data = raw
        return resp.status, headers, data
    finally:
        conn.close()


def _get(host: str, port: int, path: str, token: str = "tok") -> tuple:
    return _request("GET", host, port, path, token)


def _post(host: str, port: int, path: str, body: dict | None = None, token: str = "tok") -> tuple:
    return _request("POST", host, port, path, token, body)


def _delete(host: str, port: int, path: str, token: str = "tok") -> tuple:
    return _request("DELETE", host, port, path, token)


# ── Fixture loader ─────────────────────────────────────────────────────────────


def _load_fixture(name: str) -> dict:
    path = FIXTURES_DIR / f"{name}.json"
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ── Body-shape comparison engine ───────────────────────────────────────────────

_TYPE_SENTINELS = {
    "__str__": (str,),
    "__int__": (int,),
    "__float__": (int, float),
    "__bool__": (bool,),
    "__list__": (list,),
    "__dict__": (dict,),
}

_NULLABLE_SENTINELS = {
    "__null_or_str__": (str,),
    "__null_or_int__": (int,),
    "__null_or_dict__": (dict,),
    "__null_or_list__": (list,),
    "__null_or_float__": (int, float),
}


def _match_value(actual: object, expected: object, path: str, diffs: list[str]) -> None:
    """Recursively compare actual against expected shape; append mismatches to diffs."""
    if isinstance(expected, str) and expected.startswith("__") and expected.endswith("__"):
        if expected == "__any__":
            return
        if expected in _TYPE_SENTINELS:
            types = _TYPE_SENTINELS[expected]
            if expected == "__bool__":
                if not isinstance(actual, bool):
                    diffs.append(f"{path}: expected bool, got {type(actual).__name__} ({actual!r})")
            elif expected == "__int__":
                if not isinstance(actual, int) or isinstance(actual, bool):
                    diffs.append(f"{path}: expected int, got {type(actual).__name__} ({actual!r})")
            elif expected == "__float__":
                if not isinstance(actual, (int, float)) or isinstance(actual, bool):
                    diffs.append(f"{path}: expected float/int, got {type(actual).__name__} ({actual!r})")
            else:
                if not isinstance(actual, types):
                    diffs.append(f"{path}: expected {expected}, got {type(actual).__name__} ({actual!r})")
        elif expected in _NULLABLE_SENTINELS:
            if actual is not None:
                types = _NULLABLE_SENTINELS[expected]
                if not isinstance(actual, types) or isinstance(actual, bool):
                    allowed = " or ".join(t.__name__ for t in types)
                    diffs.append(
                        f"{path}: expected {expected} (None or {allowed}), got {type(actual).__name__} ({actual!r})"
                    )
        else:
            diffs.append(f"{path}: unknown sentinel {expected!r}")
        return

    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            diffs.append(f"{path}: expected dict, got {type(actual).__name__}")
            return
        for key, val in expected.items():
            if key not in actual:
                diffs.append(f"{path}.{key}: missing required key")
            else:
                _match_value(actual[key], val, f"{path}.{key}", diffs)
        return

    if isinstance(expected, list):
        if not isinstance(actual, list):
            diffs.append(f"{path}: expected list, got {type(actual).__name__}")
            return
        if len(expected) == 0:
            # Exact: must be empty list
            if len(actual) != 0:
                diffs.append(f"{path}: expected empty list, got {len(actual)} items")
        elif len(expected) == 1:
            # Shape template: each actual item must match expected[0]
            shape = expected[0]
            for i, item in enumerate(actual):
                _match_value(item, shape, f"{path}[{i}]", diffs)
        else:
            # Exact list comparison
            if actual != expected:
                diffs.append(f"{path}: expected {expected!r}, got {actual!r}")
        return

    # Primitive exact match (including None)
    if actual != expected:
        diffs.append(f"{path}: expected {expected!r}, got {actual!r}")


def _compare_response(
    fixture: dict,
    actual_status: int,
    actual_headers: dict,
    actual_body: object,
) -> list[str]:
    """Return list of diff strings; empty means all checks passed."""
    diffs: list[str] = []

    # Status check
    expected_status = fixture.get("status")
    if expected_status is not None and actual_status != expected_status:
        diffs.append(f"status: expected {expected_status}, got {actual_status}")

    # Content-type prefix check
    ct_prefix = fixture.get("content_type_prefix")
    if ct_prefix:
        actual_ct = actual_headers.get("content-type", "")
        if not actual_ct.startswith(ct_prefix):
            diffs.append(f"content-type: expected prefix {ct_prefix!r}, got {actual_ct!r}")

    # Body shape check
    body_shape = fixture.get("body_shape")
    if body_shape is not None:
        if not isinstance(actual_body, (dict, list)):
            diffs.append(f"body: expected JSON object/array, got {type(actual_body).__name__}")
        else:
            _match_value(actual_body, body_shape, "body", diffs)

    return diffs


# ── Individual contract tests ──────────────────────────────────────────────────


def _run_fixture(label: str, fixture_name: str, status: int, headers: dict, body: object) -> None:
    fixture = _load_fixture(fixture_name)
    diffs = _compare_response(fixture, status, headers, body)
    _record(label, not diffs, diffs)


def test_healthz(host: str, port: int, token: str = "tok") -> None:
    status, hdrs, body = _get(host, port, "/healthz", token)
    _run_fixture("healthz: GET /healthz → 200 with counts", "healthz", status, hdrs, body)


def test_discovery(host: str, port: int, token: str = "tok") -> None:
    status, hdrs, body = _get(host, port, "/.well-known/browse-host", token)
    _run_fixture(
        "discovery: GET /.well-known/browse-host → schema/caps shape",
        "discovery",
        status,
        hdrs,
        body,
    )


def test_discovery_verify_missing_ticket(host: str, port: int, token: str = "tok") -> None:
    status, hdrs, body = _post(host, port, "/.well-known/browse-host/verify", {}, token)
    _run_fixture(
        "discovery_verify: POST with empty body → valid=false",
        "discovery_verify_missing_ticket",
        status,
        hdrs,
        body,
    )


def test_sessions_list(host: str, port: int, token: str = "tok") -> None:
    status, hdrs, body = _get(host, port, "/api/sessions", token)
    _run_fixture(
        "sessions_list: GET /api/sessions → pagination envelope + item schema",
        "sessions_list",
        status,
        hdrs,
        body,
    )


def test_sessions_list_empty(host_empty: str, port_empty: int, token: str = "tok") -> None:
    status, hdrs, body = _get(host_empty, port_empty, "/api/sessions", token)
    _run_fixture(
        "sessions_list_empty: GET /api/sessions (empty DB) → items=[] total=0",
        "sessions_list_empty",
        status,
        hdrs,
        body,
    )


def test_sessions_list_page(host: str, port: int, token: str = "tok") -> None:
    status, hdrs, body = _get(host, port, "/api/sessions?page=1&page_size=2", token)
    _run_fixture(
        "sessions_list_page: GET /api/sessions?page=1&page_size=2 → has_more=true",
        "sessions_list_page",
        status,
        hdrs,
        body,
    )


def test_session_detail(host: str, port: int, token: str = "tok") -> None:
    status, hdrs, body = _get(host, port, "/api/sessions/session-id-0000-abcdef", token)
    _run_fixture(
        "session_detail: GET /api/sessions/{id} → meta+timeline shape",
        "session_detail",
        status,
        hdrs,
        body,
    )


def test_session_detail_404(host: str, port: int, token: str = "tok") -> None:
    status, hdrs, body = _get(host, port, "/api/sessions/no-such-session-exists", token)
    _run_fixture(
        "session_detail_404: GET /api/sessions/unknown → 404 SESSION_NOT_FOUND",
        "session_detail_404",
        status,
        hdrs,
        body,
    )


def test_session_detail_400(host: str, port: int, token: str = "tok") -> None:
    # Use a URL-encoded invalid ID (bad!id → bad%21id); path-traversal strings
    # are normalized by http.client before sending, so they never reach the route.
    # Use _get() so that _USE_SSL is respected (HTTPS when BROWSE_BASE_URL=https://...).
    status, hdrs, body = _get(host, port, "/api/sessions/bad%21id", token)
    _run_fixture(
        "session_detail_400: GET /api/sessions/bad%21id → 400 BAD_SESSION_ID",
        "session_detail_400",
        status,
        hdrs,
        body,
    )


def test_dashboard(host: str, port: int, token: str = "tok") -> None:
    status, hdrs, body = _get(host, port, "/api/dashboard", token)
    _run_fixture(
        "dashboard: GET /api/dashboard → totals + list shapes",
        "dashboard",
        status,
        hdrs,
        body,
    )


def test_eval_stats(host: str, port: int, token: str = "tok") -> None:
    status, hdrs, body = _get(host, port, "/api/eval/stats", token)
    _run_fixture(
        "eval_stats: GET /api/eval/stats → aggregation + recent_comments lists",
        "eval_stats",
        status,
        hdrs,
        body,
    )


def test_compare(host: str, port: int, token: str = "tok") -> None:
    path = "/api/compare?a=session-id-0000-abcdef&b=session-id-0001-abcdef"
    status, hdrs, body = _get(host, port, path, token)
    _run_fixture(
        "compare: GET /api/compare?a=&b= → a/b with session+timeline",
        "compare",
        status,
        hdrs,
        body,
    )


def test_compare_400_missing_params(host: str, port: int, token: str = "tok") -> None:
    status, hdrs, body = _get(host, port, "/api/compare", token)
    _run_fixture(
        "compare_400_missing: GET /api/compare (no params) → 400 MISSING_PARAMS",
        "compare_400_missing_params",
        status,
        hdrs,
        body,
    )


def test_compare_400_invalid_id(host: str, port: int, token: str = "tok") -> None:
    status, hdrs, body = _get(
        host,
        port,
        "/api/compare?a=../etc/passwd&b=session-id-0000-abcdef",
        token,
    )
    _run_fixture(
        "compare_400_invalid: GET /api/compare?a=../bad → 400 BAD_SESSION_ID",
        "compare_400_invalid_id",
        status,
        hdrs,
        body,
    )


def test_embeddings_503(host: str, port: int, token: str = "tok") -> None:
    status, hdrs, body = _get(host, port, "/api/embeddings", token)
    fixture = _load_fixture("embeddings_503")
    if status == 200:
        # Empty DB may return 200 with empty points — also acceptable
        alt_diffs: list[str] = []
        _match_value(body, {"points": "__list__", "method": "__str__"}, "body", alt_diffs)
        ok = not alt_diffs
        _record(
            "embeddings_503: GET /api/embeddings → 503 or 200-empty",
            ok,
            alt_diffs if not ok else None,
        )
    else:
        diffs = _compare_response(fixture, status, hdrs, body)
        _record("embeddings_503: GET /api/embeddings → 503 (no embeddings)", not diffs, diffs)


def test_sync_status(host: str, port: int, token: str = "tok") -> None:
    status, hdrs, body = _get(host, port, "/api/sync/status", token)
    _run_fixture(
        "sync_status: GET /api/sync/status → rollout/runtime/operator_actions shape",
        "sync_status",
        status,
        hdrs,
        body,
    )


def test_scout_status(host: str, port: int, token: str = "tok") -> None:
    status, hdrs, body = _get(host, port, "/api/scout/status", token)
    _run_fixture(
        "scout_status: GET /api/scout/status → config/analysis/grace_window shape",
        "scout_status",
        status,
        hdrs,
        body,
    )


def test_tentacles_status(host: str, port: int, token: str = "tok") -> None:
    status, hdrs, body = _get(host, port, "/api/tentacles/status", token)
    _run_fixture(
        "tentacles_status: GET /api/tentacles/status → marker/audit/runtime shape",
        "tentacles_status",
        status,
        hdrs,
        body,
    )


def test_skills_metrics(host: str, port: int, token: str = "tok") -> None:
    status, hdrs, body = _get(host, port, "/api/skills/metrics", token)
    _run_fixture(
        "skills_metrics: GET /api/skills/metrics → tables/summary/outcomes shape",
        "skills_metrics",
        status,
        hdrs,
        body,
    )


def test_skills_catalog(host: str, port: int, token: str = "tok") -> None:
    status, hdrs, body = _get(host, port, "/api/skills/catalog", token)
    _run_fixture(
        "skills_catalog: GET /api/skills/catalog → skills list + sources + runtime",
        "skills_catalog",
        status,
        hdrs,
        body,
    )


def test_knowledge_insights_503(host: str, port: int, token: str = "tok") -> None:
    """Requires Python mock to set script path to a non-existent file."""
    import browse.api.insights as _insights_mod

    with unittest.mock.patch.object(_insights_mod, "_HEALTH_SCRIPT", Path("/nonexistent/knowledge-health.py")):
        status, hdrs, body = _get(host, port, "/api/knowledge/insights", token)
    _run_fixture(
        "knowledge_insights_503: GET /api/knowledge/insights (no script) → 503",
        "knowledge_insights_503",
        status,
        hdrs,
        body,
    )


def test_workflow_health_503(host: str, port: int, token: str = "tok") -> None:
    """Requires Python mock to set script path to a non-existent file."""
    import browse.api.workflow as _wf_mod

    with unittest.mock.patch.object(_wf_mod, "_HEALTH_SCRIPT", Path("/nonexistent/workflow-health.py")):
        status, hdrs, body = _get(host, port, "/api/workflow/health", token)
    _run_fixture(
        "workflow_health_503: GET /api/workflow/health (no script) → 503",
        "workflow_health_503",
        status,
        hdrs,
        body,
    )


def test_retro_summary_503(host: str, port: int, token: str = "tok") -> None:
    """Requires Python mock to set script path to a non-existent file."""
    import browse.api.retro as _retro_mod

    with unittest.mock.patch.object(_retro_mod, "_RETRO_SCRIPT", Path("/nonexistent/retro.py")):
        status, hdrs, body = _get(host, port, "/api/retro/summary", token)
    _run_fixture(
        "retro_summary_503: GET /api/retro/summary (no script) → 503",
        "retro_summary_503",
        status,
        hdrs,
        body,
    )


def test_pairing_static_no_slot(host: str, port: int, token: str = "tok") -> None:
    status, hdrs, body = _get(host, port, "/api/operator/pairing/static", token)
    _run_fixture(
        "pairing_static_no_slot: GET /api/operator/pairing/static → active=false",
        "pairing_static_no_slot",
        status,
        hdrs,
        body,
    )


def test_pairing_verify_bad_body(host: str, port: int, token: str = "tok") -> None:
    status, hdrs, body = _post(host, port, "/api/operator/pairing/verify", None, token)
    _run_fixture(
        "pairing_verify_bad_body: POST /api/operator/pairing/verify (empty) → 400",
        "pairing_verify_bad_body",
        status,
        hdrs,
        body,
    )


def test_research_pack_missing(host: str, port: int, token: str = "tok") -> None:
    """Requires Python mock to set pack path to a non-existent file."""
    import browse.routes.scout as _scout_mod

    with unittest.mock.patch.object(
        _scout_mod, "_RESEARCH_PACK_PATH", Path("/nonexistent/.trend-scout-research-pack.json")
    ):
        status, hdrs, body = _get(host, port, "/api/scout/research-pack", token)
    _run_fixture(
        "research_pack_missing: GET /api/scout/research-pack (no file) → available=false",
        "research_pack_missing",
        status,
        hdrs,
        body,
    )


# ── Operator action contract (cross-route) ─────────────────────────────────────


def test_operator_action_contract(host: str, port: int, token: str = "tok") -> None:
    """All routes with operator_actions must have the required five fields on each action."""
    required = {"id", "title", "description", "command", "safe"}
    for path in [
        "/api/sync/status",
        "/api/scout/status",
        "/api/tentacles/status",
        "/api/skills/metrics",
    ]:
        _, _, body = _get(host, port, path, token)
        label = path.split("/")[2]
        actions = (body.get("operator_actions") or []) if isinstance(body, dict) else []
        ok = all(isinstance(a, dict) and required.issubset(a.keys()) for a in actions)
        _record(
            f"operator_action_contract: {label} all actions have {required!r}",
            ok,
            [
                f"{label} action missing fields: {a}"
                for a in actions
                if not isinstance(a, dict) or not required.issubset(a.keys())
            ]
            if not ok
            else None,
        )
        ok2 = all(isinstance(a, dict) and a.get("safe") is True for a in actions)
        _record(
            f"operator_action_contract: {label} all actions have safe=true",
            ok2,
            [f"{label} action safe!=true: {a!r}" for a in actions if isinstance(a, dict) and a.get("safe") is not True]
            if not ok2
            else None,
        )


# ── Matcher unit tests (no server required) ────────────────────────────────────


def test_match_value_unit() -> None:
    """Unit tests for _match_value sentinel logic — runs without a live server."""

    def _check(actual: object, expected: object, should_pass: bool, label: str) -> None:
        diffs: list[str] = []
        _match_value(actual, expected, "x", diffs)
        passed = len(diffs) == 0
        _record(
            f"match_value_unit: {label}",
            passed == should_pass,
            [f"unexpected {'pass' if passed else 'fail'}: {diffs}"] if passed != should_pass else None,
        )

    # __null_or_float__ — regression: booleans must be rejected (Python bool is subclass of int)
    _check(None, "__null_or_float__", True, "__null_or_float__ accepts None")
    _check(1, "__null_or_float__", True, "__null_or_float__ accepts int 1")
    _check(1.5, "__null_or_float__", True, "__null_or_float__ accepts float 1.5")
    _check(True, "__null_or_float__", False, "__null_or_float__ rejects True")
    _check(False, "__null_or_float__", False, "__null_or_float__ rejects False")

    # __null_or_int__ — existing bool guard should still hold
    _check(None, "__null_or_int__", True, "__null_or_int__ accepts None")
    _check(42, "__null_or_int__", True, "__null_or_int__ accepts int 42")
    _check(True, "__null_or_int__", False, "__null_or_int__ rejects True")
    _check(False, "__null_or_int__", False, "__null_or_int__ rejects False")

    # __float__ (non-nullable) — should still reject booleans
    _check(3.14, "__float__", True, "__float__ accepts float")
    _check(2, "__float__", True, "__float__ accepts int")
    _check(True, "__float__", False, "__float__ rejects True")

    # Mismatch message for __null_or_float__ must list all allowed types (int and float)
    diffs_msg: list[str] = []
    _match_value("bad", "__null_or_float__", "x", diffs_msg)
    msg_ok = diffs_msg and "int or float" in diffs_msg[0]
    _record(
        "match_value_unit: __null_or_float__ mismatch message includes 'int or float'",
        bool(msg_ok),
        [f"message was: {diffs_msg}"] if not msg_ok else None,
    )


def test_ssl_selection_unit() -> None:
    """Unit test: _request() (and therefore test_session_detail_400) picks
    HTTPSConnection when _USE_SSL is True, without hitting a live network."""
    global _USE_SSL
    original_use_ssl = _USE_SSL
    try:
        _USE_SSL = True
        stub_resp = unittest.mock.MagicMock()
        stub_resp.status = 200
        stub_resp.read.return_value = b"{}"
        stub_resp.getheaders.return_value = []
        stub_conn = unittest.mock.MagicMock()
        stub_conn.getresponse.return_value = stub_resp
        with (
            unittest.mock.patch.object(http.client, "HTTPSConnection", return_value=stub_conn) as mock_https,
            unittest.mock.patch.object(http.client, "HTTPConnection") as mock_http,
        ):
            _request("GET", "example.com", 443, "/test")
        ok = mock_https.called and not mock_http.called
        _record(
            "ssl_selection_unit: _request uses HTTPSConnection when _USE_SSL=True",
            ok,
            [f"https_called={mock_https.called} http_called={mock_http.called}"] if not ok else None,
        )
    finally:
        _USE_SSL = original_use_ssl


# ── --update mode: capture Python responses as new fixture files ───────────────


def _make_sentinel(value: object) -> object:
    """Replace a value with a type sentinel for fixture serialization."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return "__int__"
    if isinstance(value, float):
        return "__float__"
    if isinstance(value, str):
        return "__str__"
    if isinstance(value, dict):
        return {k: _make_sentinel(v) for k, v in value.items()}
    if isinstance(value, list):
        if not value:
            return []
        return [_make_sentinel(value[0])]
    return "__any__"


def _update_fixture(
    name: str,
    endpoint: str,
    status: int,
    headers: dict,
    body: object,
    dynamic_keys: list[str] | None = None,
) -> None:
    """Write or overwrite a fixture file with live response data."""
    fixture: dict = {
        "_meta": {
            "id": name,
            "endpoint": endpoint,
            "description": f"Auto-captured from Python backend — {endpoint}",
        },
        "status": status,
        "content_type_prefix": headers.get("content-type", "application/json").split(";")[0],
    }
    if isinstance(body, dict):
        body_shape = {}
        for k, v in body.items():
            if dynamic_keys and k in dynamic_keys:
                body_shape[k] = _make_sentinel(v)
            else:
                body_shape[k] = v
        fixture["body_shape"] = body_shape
    elif isinstance(body, list):
        fixture["body_shape"] = [_make_sentinel(body[0])] if body else []

    out = FIXTURES_DIR / f"{name}.json"
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(fixture, fh, indent=2, default=str)
    print(f"  UPDATED  {out.name}")


# ── Test runner ────────────────────────────────────────────────────────────────


def run_tests(external_url: str | None = None) -> int:
    global _PASS, _FAIL
    _PASS = 0
    _FAIL = 0

    if external_url:
        from urllib.parse import urlparse

        parsed = urlparse(external_url)
        global _USE_SSL
        _USE_SSL = parsed.scheme == "https"
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if _USE_SSL else 80)
        token = os.environ.get("BROWSE_TOKEN", "tok")
        print(f"=== test_browse_api_contract.py (external: {external_url}) ===")
        # External backend: run pure HTTP contract tests only (no Python mocks)
        db = server = None
        db_empty = server_empty = None
        try:
            test_healthz(host, port, token)
            test_discovery(host, port, token)
            test_discovery_verify_missing_ticket(host, port, token)
            test_sessions_list(host, port, token)
            test_sessions_list_page(host, port, token)
            test_session_detail_404(host, port, token)
            test_session_detail_400(host, port, token)
            test_eval_stats(host, port, token)
            test_compare_400_missing_params(host, port, token)
            test_compare_400_invalid_id(host, port, token)
            test_pairing_static_no_slot(host, port, token)
            test_pairing_verify_bad_body(host, port, token)
        finally:
            pass
    else:
        print("=== test_browse_api_contract.py (Python backend) ===")
        db = _make_test_db()
        server, host, port = _start_server(db)
        db_empty = _make_empty_db()
        server_empty, host_empty, port_empty = _start_server(db_empty)
        try:
            test_match_value_unit()
            test_ssl_selection_unit()
            test_healthz(host, port)
            test_discovery(host, port)
            test_discovery_verify_missing_ticket(host, port)
            test_sessions_list(host, port)
            test_sessions_list_empty(host_empty, port_empty)
            test_sessions_list_page(host, port)
            test_session_detail(host, port)
            test_session_detail_404(host, port)
            test_session_detail_400(host, port)
            test_dashboard(host, port)
            test_eval_stats(host, port)
            test_compare(host, port)
            test_compare_400_missing_params(host, port)
            test_compare_400_invalid_id(host, port)
            test_embeddings_503(host, port)
            test_sync_status(host, port)
            test_scout_status(host, port)
            test_tentacles_status(host, port)
            test_skills_metrics(host, port)
            test_skills_catalog(host, port)
            test_knowledge_insights_503(host, port)
            test_workflow_health_503(host, port)
            test_retro_summary_503(host, port)
            test_pairing_static_no_slot(host, port)
            test_pairing_verify_bad_body(host, port)
            test_research_pack_missing(host, port)
            test_operator_action_contract(host, port)
        finally:
            server.shutdown()
            server_empty.shutdown()

    print(f"\n{'=' * 50}")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    return _FAIL


def run_update(token: str = "tok") -> None:
    """Capture live Python responses and overwrite a subset of fixture files.

    Updates: healthz, discovery, eval_stats, dashboard, sessions_list_empty.
    Other fixtures must be updated manually or by extending this function.
    """
    print("=== test_browse_api_contract.py --update (capturing fixtures) ===")
    db = _make_test_db()
    server, host, port = _start_server(db)
    db_empty = _make_empty_db()
    server_empty, host_empty, port_empty = _start_server(db_empty)
    try:
        _update_fixture(
            "healthz",
            "GET /healthz",
            *_get(host, port, "/healthz"),
            dynamic_keys=["schema_version", "sessions", "knowledge_entries", "last_indexed_at"],
        )
        _update_fixture(
            "discovery",
            "GET /.well-known/browse-host",
            *_get(host, port, "/.well-known/browse-host"),
            dynamic_keys=["static_mode_active", "demo_mode_badge", "cors_origins_configured"],
        )
        _update_fixture("eval_stats", "GET /api/eval/stats", *_get(host, port, "/api/eval/stats"))
        _update_fixture(
            "dashboard",
            "GET /api/dashboard",
            *_get(host, port, "/api/dashboard"),
            dynamic_keys=list(_load_fixture("dashboard").get("body_shape", {}).keys()),
        )
        _update_fixture(
            "sessions_list_empty", "GET /api/sessions (empty DB)", *_get(host_empty, port_empty, "/api/sessions")
        )
    finally:
        server.shutdown()
        server_empty.shutdown()
    print("\nFixtures updated. Review changes before committing.")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--update" in args:
        run_update()
    else:
        external = os.environ.get("BROWSE_BASE_URL", "").strip() or None
        rc = run_tests(external_url=external)
        sys.exit(rc)
