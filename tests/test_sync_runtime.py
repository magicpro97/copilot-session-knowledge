#!/usr/bin/env python3
"""
test_sync_runtime.py — Focused regression coverage for sync runtime client surfaces.

Run:
    python3 test_sync_runtime.py
"""

import importlib.util
import json
import os
import sqlite3
import stat
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO = Path(__file__).parent.parent
ARTIFACT_DIR = REPO / ".sync-runtime-test-artifacts"

PASS = 0
FAIL = 0


def test(name: str, passed: bool, detail: str = ""):
    global PASS, FAIL
    if passed:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, str(REPO / filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def reset_artifacts():
    if ARTIFACT_DIR.exists():
        for p in sorted(ARTIFACT_DIR.rglob("*"), reverse=True):
            if p.is_file():
                p.unlink(missing_ok=True)
            elif p.is_dir():
                p.rmdir()
        ARTIFACT_DIR.rmdir()
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)


def make_db(db_path: Path):
    db = sqlite3.connect(str(db_path))
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            summary TEXT DEFAULT '',
            total_checkpoints INTEGER DEFAULT 0,
            total_research INTEGER DEFAULT 0,
            total_files INTEGER DEFAULT 0,
            has_plan INTEGER DEFAULT 0,
            source TEXT DEFAULT 'copilot',
            indexed_at TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS search_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT,
            result_id TEXT,
            result_kind TEXT,
            verdict INTEGER NOT NULL CHECK(verdict IN (-1,0,1)),
            comment TEXT,
            user_agent TEXT,
            created_at TEXT NOT NULL,
            origin_replica_id TEXT DEFAULT 'local',
            stable_id TEXT
        );
        """
    )
    db.commit()
    db.close()


def make_real_schema_db(db_path: Path):
    build_session_index = load_module("build_session_index_schema", "build-session-index.py")
    extract_knowledge = load_module("extract_knowledge_schema", "extract-knowledge.py")
    db = build_session_index.create_db(db_path)
    extract_knowledge.ensure_tables(db)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS search_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT,
            result_id TEXT,
            result_kind TEXT,
            verdict INTEGER NOT NULL CHECK(verdict IN (-1,0,1)),
            comment TEXT,
            user_agent TEXT,
            created_at TEXT NOT NULL,
            origin_replica_id TEXT DEFAULT 'local',
            stable_id TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sf_query ON search_feedback(query);
        CREATE INDEX IF NOT EXISTS idx_sf_created ON search_feedback(created_at);
        CREATE INDEX IF NOT EXISTS idx_sf_stable_id ON search_feedback(stable_id);
        CREATE INDEX IF NOT EXISTS idx_sf_origin_replica ON search_feedback(origin_replica_id);
        """
    )
    db.commit()
    db.close()


class _GatewayState:
    def __init__(self):
        self.pushed_payloads = []
        self.pull_requested = []
        self.pull_txns = [
            {
                "txn_id": "remote-txn-1",
                "replica_id": "remote-a",
                "created_at": "2026-01-01T00:00:00Z",
                "committed_at": "2026-01-01T00:00:00Z",
                "status": "pending",
                "ops": [
                    {
                        "table_name": "sessions",
                        "op_type": "upsert",
                        "row_stable_id": "session-remote-1",
                        "row_payload": {
                            "id": "session-remote-1",
                            "path": "/repo/remote",
                            "summary": "remote row",
                            "total_checkpoints": 1,
                            "total_research": 0,
                            "total_files": 0,
                            "has_plan": 0,
                            "source": "sync",
                            "indexed_at": "2026-01-01T00:00:00Z",
                        },
                        "op_index": 0,
                        "created_at": "2026-01-01T00:00:00Z",
                    }
                ],
            }
        ]


def make_gateway_handler(state: _GatewayState):
    class Handler(BaseHTTPRequestHandler):
        def _write(self, code: int, payload: dict):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            return

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/healthz":
                self._write(200, {"status": "ok"})
                return
            if parsed.path == "/sync/pull":
                q = parse_qs(parsed.query)
                state.pull_requested.append(q)
                txns = state.pull_txns
                next_after = txns[-1]["txn_id"] if txns else ""
                self._write(200, {"txns": txns, "next_after": next_after, "has_more": False})
                return
            self._write(404, {"error": "not found"})

        def do_POST(self):
            parsed = urlparse(self.path)
            if parsed.path != "/sync/push":
                self._write(404, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            body = json.loads(raw or "{}")
            state.pushed_payloads.append(body)
            txn_ids = [str(t.get("txn_id", "")) for t in body.get("txns", [])]
            self._write(
                200,
                {"accepted_txn_ids": txn_ids, "duplicate_txn_ids": [], "latest_txn_id": txn_ids[-1] if txn_ids else ""},
            )

    return Handler


def with_gateway():
    state = _GatewayState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_gateway_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    return server, thread, state, base_url


print("\n🔧 sync-config.py")
reset_artifacts()
sync_config = load_module("sync_config_test", "sync-config.py")
sync_config.TOOLS_DIR = ARTIFACT_DIR
sync_config.CONFIG_PATH = ARTIFACT_DIR / "sync-config.json"

saved = sync_config.set_connection_string("https://gateway.example.com/")
status = sync_config.get_status()

test("sync-config normalizes URL", saved == "https://gateway.example.com")
test("sync-config status configured", status["configured"] is True)
test("sync-config stores connection string", status["connection_string"] == "https://gateway.example.com")
if os.name != "nt":
    mode = stat.S_IMODE(sync_config.CONFIG_PATH.stat().st_mode)
    test("sync-config writes 0o600 permissions", mode == 0o600, f"mode={oct(mode)}")


print("\n🔁 sync-daemon.py + sync-status.py + knowledge-health.py + sync-knowledge.py")

db_path = ARTIFACT_DIR / "knowledge.db"
make_db(db_path)

sync_daemon = load_module("sync_daemon_test", "sync-daemon.py")
sync_status = load_module("sync_status_test", "sync-status.py")
knowledge_health = load_module("knowledge_health_test", "knowledge-health.py")
sync_knowledge = load_module("sync_knowledge_test", "sync-knowledge.py")

# Point modules to artifact paths.
sync_daemon.DB_PATH = db_path
sync_daemon.SESSION_STATE = ARTIFACT_DIR
sync_daemon.STATE_FILE = ARTIFACT_DIR / ".sync-daemon-state.json"
sync_daemon.LOCK_FILE = ARTIFACT_DIR / ".sync-daemon.lock"
sync_daemon.SYNC_CONFIG_PATH = ARTIFACT_DIR / "sync-config.json"
sync_status.DB_PATH = db_path
sync_status.CONFIG_PATH = ARTIFACT_DIR / "sync-config.json"
knowledge_health.DB_PATH = db_path
sync_knowledge.DB_PATH = db_path
sync_knowledge.SYNC_CONFIG_PATH = ARTIFACT_DIR / "sync-config.json"

# Ensure schema repair path works.
db = sqlite3.connect(str(db_path))
sync_knowledge.ensure_sync_runtime_schema(db)
db.commit()
db.close()

# Seed one pending local transaction.
db = sqlite3.connect(str(db_path))
db.execute("INSERT OR REPLACE INTO sync_state (key, value) VALUES ('local_replica_id', 'local-test')")
db.execute(
    "INSERT INTO sync_txns (txn_id, replica_id, status, created_at, committed_at) VALUES (?, ?, 'pending', ?, '')",
    ("local-txn-1", "local-test", datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")),
)
db.execute(
    """
    INSERT INTO sync_ops (txn_id, table_name, op_type, row_stable_id, row_payload, op_index, created_at)
    VALUES (?, 'sessions', 'upsert', 'session-local-1', ?, 0, ?)
    """,
    (
        "local-txn-1",
        json.dumps(
            {
                "id": "session-local-1",
                "path": "/repo/local",
                "summary": "local row",
                "total_checkpoints": 1,
                "total_research": 0,
                "total_files": 0,
                "has_plan": 1,
                "source": "copilot",
                "indexed_at": "2026-01-01T00:00:00Z",
            }
        ),
        datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    ),
)
db.commit()
db.close()

# Verify machine-specific local_replica_id seeding and migration from legacy "local".
db = sqlite3.connect(str(db_path))
db.row_factory = sqlite3.Row
db.execute("DELETE FROM sync_state WHERE key='local_replica_id'")
seeded_id = sync_daemon.get_local_replica_id(db)
test(
    "sync-daemon seeds machine-specific replica id", seeded_id.startswith("local-") and seeded_id != "local", seeded_id
)
stored_seeded = db.execute("SELECT value FROM sync_state WHERE key='local_replica_id'").fetchone()
test(
    "sync-daemon persists seeded replica id",
    stored_seeded is not None and stored_seeded[0] == seeded_id,
    str(stored_seeded),
)
db.execute("UPDATE sync_state SET value='local' WHERE key='local_replica_id'")
migrated_id = sync_daemon.get_local_replica_id(db)
test(
    "sync-daemon migrates legacy local replica id",
    migrated_id.startswith("local-") and migrated_id != "local",
    migrated_id,
)
db.commit()
db.close()

# Re-seed deterministic test replica for the rest of assertions.
db = sqlite3.connect(str(db_path))
db.execute("INSERT OR REPLACE INTO sync_state (key, value) VALUES ('local_replica_id', 'local-test')")
db.commit()
db.close()

# Foundation setup should be write-stable once the sync schema is already initialized.
db = sqlite3.connect(str(db_path))
db.row_factory = sqlite3.Row
sync_daemon.ensure_sync_foundation(db)
db.commit()
changes_before = db.total_changes
sync_daemon.ensure_sync_foundation(db)
db.commit()
changes_after = db.total_changes
test(
    "sync-daemon foundation avoids redundant writes once initialized",
    changes_after == changes_before,
    f"changes {changes_before}->{changes_after}",
)
for txn_id, txn_replica, committed_at in [
    ("remote-pending-filter", "remote-replica", "2026-01-01T00:00:00Z"),
    ("local-pending-filter", "local-test", ""),
]:
    db.execute(
        "INSERT INTO sync_txns (txn_id, replica_id, status, created_at, committed_at) VALUES (?, ?, 'pending', '2026-01-01T00:00:00Z', ?)",
        (txn_id, txn_replica, committed_at),
    )
    db.execute(
        """
        INSERT INTO sync_ops (txn_id, table_name, op_type, row_stable_id, row_payload, op_index, created_at)
        VALUES (?, 'sessions', 'upsert', ?, '{}', 0, '2026-01-01T00:00:00Z')
        """,
        (txn_id, txn_id),
    )
repaired = sync_daemon.repair_nonlocal_committed_txns(db, "local-test")
remote_status = db.execute("SELECT status FROM sync_txns WHERE txn_id='remote-pending-filter'").fetchone()
pending_ids = [t["txn_id"] for t in sync_daemon.collect_pending_txns(db, limit=20, replica_id="local-test")]
test(
    "sync-daemon repairs committed nonlocal pending txns",
    repaired >= 1 and remote_status is not None and remote_status[0] == "committed",
    f"repaired={repaired} remote_status={remote_status}",
)
test(
    "sync-daemon only collects local pending txns for push",
    "local-pending-filter" in pending_ids and "remote-pending-filter" not in pending_ids,
    str(pending_ids),
)
db.execute("DELETE FROM sync_ops WHERE txn_id IN ('remote-pending-filter', 'local-pending-filter')")
db.execute("DELETE FROM sync_txns WHERE txn_id IN ('remote-pending-filter', 'local-pending-filter')")
db.commit()
db.close()

# DB-open failures should degrade a cycle instead of crashing the background loop.
original_get_db = sync_daemon.get_db
try:

    def _raise_locked(_db_path=sync_daemon.DB_PATH):
        raise sqlite3.OperationalError("database is locked")

    sync_daemon.get_db = _raise_locked
    locked_cycle = sync_daemon.run_sync_cycle(
        db_path=db_path,
        base_url="http://127.0.0.1:1",
        limit=10,
    )
    test(
        "sync-daemon degrades when DB open is locked",
        locked_cycle["ok"] is False and "database is locked" in locked_cycle["error"],
        str(locked_cycle),
    )
finally:
    sync_daemon.get_db = original_get_db

original_gateway_health = sync_daemon.gateway_health
try:

    def _raise_timeout(_base_url):
        raise TimeoutError("The read operation timed out")

    sync_daemon.gateway_health = _raise_timeout
    timeout_cycle = sync_daemon.run_sync_cycle(
        db_path=db_path,
        base_url="https://example.invalid",
        limit=10,
    )
    test(
        "sync-daemon degrades when gateway health times out",
        timeout_cycle["ok"] is False and "timed out" in timeout_cycle["error"],
        str(timeout_cycle),
    )
finally:
    sync_daemon.gateway_health = original_gateway_health

server, thread, gateway_state, base_url = with_gateway()
try:
    (ARTIFACT_DIR / "sync-config.json").write_text(
        json.dumps({"connection_string": base_url}, indent=2),
        encoding="utf-8",
    )
    if os.name != "nt":
        os.chmod(ARTIFACT_DIR / "sync-config.json", 0o600)

    cycle = sync_daemon.run_sync_cycle(db_path=db_path, base_url=base_url, limit=10)
    test("sync-daemon cycle succeeds", cycle["ok"] is True, cycle.get("error", ""))
    test("sync-daemon pushes pending txn", len(gateway_state.pushed_payloads) == 1)
    pushed_txns = gateway_state.pushed_payloads[0].get("txns", []) if gateway_state.pushed_payloads else []
    test("sync-daemon pushed local-txn-1", pushed_txns and pushed_txns[0].get("txn_id") == "local-txn-1")

    db = sqlite3.connect(str(db_path))
    row = db.execute("SELECT status FROM sync_txns WHERE txn_id='local-txn-1'").fetchone()
    test("local txn marked committed", row is not None and row[0] == "committed", f"row={row}")
    remote_row = db.execute("SELECT id, summary FROM sessions WHERE id='session-remote-1'").fetchone()
    test("remote pull applied to sessions", remote_row is not None and remote_row[1] == "remote row")
    remote_txn_status = db.execute("SELECT status FROM sync_txns WHERE txn_id='remote-txn-1'").fetchone()
    test(
        "remote pulled txn is stored committed locally",
        remote_txn_status is not None and remote_txn_status[0] == "committed",
        str(remote_txn_status),
    )
    db.close()

    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    for txn_id, created_at in [
        ("local-txn-response-check", "2026-02-01T00:00:00Z"),
        ("local-txn-unsent", "2026-02-01T00:00:01Z"),
    ]:
        db.execute(
            "INSERT INTO sync_txns (txn_id, replica_id, status, created_at, committed_at) VALUES (?, ?, 'pending', ?, '')",
            (txn_id, "local-test", created_at),
        )
        db.execute(
            """
            INSERT INTO sync_ops (txn_id, table_name, op_type, row_stable_id, row_payload, op_index, created_at)
            VALUES (?, 'sessions', 'upsert', ?, ?, 0, ?)
            """,
            (
                txn_id,
                f"session-{txn_id}",
                json.dumps(
                    {
                        "id": f"session-{txn_id}",
                        "path": f"/repo/{txn_id}",
                        "summary": txn_id,
                        "indexed_at": now,
                    }
                ),
                now,
            ),
        )
    db.commit()

    original_request_json = sync_daemon._request_json
    try:

        def _phantom_push_response(*_args, **_kwargs):
            return {
                "accepted_txn_ids": ["local-txn-response-check", "local-txn-unsent"],
                "duplicate_txn_ids": [],
                "latest_txn_id": "local-txn-response-check",
            }

        sync_daemon._request_json = _phantom_push_response
        try:
            sync_daemon.push_once(db, "http://sync.test", "local-test", limit=1)
            phantom_rejected = False
            phantom_error = ""
        except ValueError as exc:
            phantom_rejected = "unsent txn_ids" in str(exc)
            phantom_error = str(exc)
        rows = db.execute(
            """
            SELECT txn_id, status
            FROM sync_txns
            WHERE txn_id IN ('local-txn-response-check', 'local-txn-unsent')
            ORDER BY txn_id
            """
        ).fetchall()
        test("sync-daemon rejects gateway txn_ids not in pushed batch", phantom_rejected, phantom_error)
        test(
            "unsent txn_ids remain pending after rejected gateway response",
            all(r[1] == "pending" for r in rows),
            str(rows),
        )
    finally:
        sync_daemon._request_json = original_request_json
        db.execute("DELETE FROM sync_ops WHERE txn_id IN ('local-txn-response-check', 'local-txn-unsent')")
        db.execute("DELETE FROM sync_txns WHERE txn_id IN ('local-txn-response-check', 'local-txn-unsent')")
        db.commit()
        db.close()

    status_obj = sync_status.collect_status(db_path=db_path, check_health=True)
    test("sync-status finds local replica id", status_obj["local_replica_id"] == "local-test")
    test(
        "sync-status gateway reachable",
        status_obj["gateway_health"]["status"] == "ok",
        str(status_obj["gateway_health"]),
    )
    test("sync-status keeps http-gateway client contract", status_obj["client_contract"] == "http-gateway")
    test("sync-status direct DB sync remains disabled", status_obj["direct_db_sync"] is False)
    test(
        "sync-status classifies localhost gateway as reference/mock",
        status_obj["gateway_target"] == "reference-mock",
        str(status_obj["gateway_target"]),
    )

    sync_stats = knowledge_health.compute_sync_stats()
    test("knowledge-health sync stats available", sync_stats["available"] is True)
    test("knowledge-health sync cursor set", sync_stats["cursor_txn_id"] == "remote-txn-1", str(sync_stats))
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)

# pull/apply fail-open for unknown tables + stable_id_column validation + marker consumption.
db2_path = ARTIFACT_DIR / "knowledge-advanced.db"
make_db(db2_path)
db2 = sqlite3.connect(str(db2_path))
db2.row_factory = sqlite3.Row
sync_daemon.ensure_sync_foundation(db2)
db2.commit()
db2.close()

server2, thread2, gateway_state2, base_url2 = with_gateway()
gateway_state2.pull_txns = [
    {
        "txn_id": "remote-unknown-1",
        "replica_id": "remote-b",
        "created_at": "2026-02-01T00:00:00Z",
        "committed_at": "2026-02-01T00:00:00Z",
        "status": "committed",
        "ops": [
            {
                "table_name": "future_table",
                "op_type": "upsert",
                "row_stable_id": "future-1",
                "row_payload": {"stable_id": "future-1", "payload": "ignored"},
                "op_index": 0,
                "created_at": "2026-02-01T00:00:00Z",
            }
        ],
    },
    {
        "txn_id": "remote-unknown-2",
        "replica_id": "remote-b",
        "created_at": "2026-02-01T00:00:01Z",
        "committed_at": "2026-02-01T00:00:01Z",
        "status": "committed",
        "ops": [
            {
                "table_name": "sessions",
                "op_type": "upsert",
                "row_stable_id": "session-remote-2",
                "row_payload": {
                    "id": "session-remote-2",
                    "path": "/repo/remote-2",
                    "summary": "remote row 2",
                    "total_checkpoints": 1,
                    "total_research": 0,
                    "total_files": 0,
                    "has_plan": 0,
                    "source": "sync",
                    "indexed_at": "2026-02-01T00:00:01Z",
                },
                "op_index": 0,
                "created_at": "2026-02-01T00:00:01Z",
            }
        ],
    },
]
try:
    db2 = sync_daemon.get_db(db2_path)
    pull_result = sync_daemon.pull_once(db2, base_url2, "local-runtime-2", limit=10)
    db2.commit()
    remote2 = db2.execute("SELECT summary FROM sessions WHERE id='session-remote-2'").fetchone()
    cursor2 = db2.execute("SELECT last_txn_id FROM sync_cursors WHERE replica_id='local-runtime-2'").fetchone()
    test("pull fail-open ignores unknown future table", pull_result["applied"] == 2, str(pull_result))
    test("pull still applies known canonical op", remote2 is not None and remote2[0] == "remote row 2", str(remote2))
    test(
        "pull cursor advances after unknown table op",
        cursor2 is not None and cursor2[0] == "remote-unknown-2",
        str(cursor2),
    )

    db2.execute("UPDATE sync_table_policies SET stable_id_column='bad-col' WHERE table_name='sessions'")
    sync_daemon._apply_op(
        db2,
        {
            "table_name": "sessions",
            "op_type": "upsert",
            "row_stable_id": "session-invalid-stable-col",
            "row_payload": {
                "id": "session-invalid-stable-col",
                "path": "/repo/bad-stable-col",
                "summary": "stable col fallback",
            },
        },
    )
    fallback_row = db2.execute("SELECT summary FROM sessions WHERE id='session-invalid-stable-col'").fetchone()
    test(
        "invalid stable_id_column is validated and does not break apply",
        fallback_row is not None and fallback_row[0] == "stable col fallback",
        str(fallback_row),
    )
    db2.commit()
    db2.close()
finally:
    server2.shutdown()
    server2.server_close()
    thread2.join(timeout=2)

# pull/apply contract: portable stable references must resolve against real schema surfaces.
db4_path = ARTIFACT_DIR / "knowledge-real-schema.db"
make_real_schema_db(db4_path)
db4 = sqlite3.connect(str(db4_path))
db4.row_factory = sqlite3.Row
sync_daemon.ensure_sync_foundation(db4)
db4.execute(
    """
    INSERT INTO search_feedback (query, result_id, result_kind, verdict, comment, user_agent, created_at, origin_replica_id, stable_id)
    VALUES ('empty-stable', 'seed', 'knowledge', 0, 'seed row', 'local-test', '2026-02-01T00:00:00Z', 'local', '')
    """
)
db4.commit()
db4.close()

server4, thread4, gateway_state4, base_url4 = with_gateway()
gateway_state4.pull_txns = [
    {
        "txn_id": "remote-contract-1",
        "replica_id": "remote-contract",
        "created_at": "2026-02-02T00:00:00Z",
        "committed_at": "2026-02-02T00:00:00Z",
        "status": "committed",
        "ops": [
            {
                "table_name": "sessions",
                "op_type": "upsert",
                "row_stable_id": "session-contract-1",
                "row_payload": {
                    "id": "remote-session-id-ignored",
                    "path": "/repo/contract-session",
                    "summary": "contract session",
                    "total_checkpoints": 2,
                    "total_research": 1,
                    "total_files": 3,
                    "has_plan": 1,
                    "source": "sync",
                    "indexed_at": "2026-02-02T00:00:00Z",
                },
                "op_index": 0,
                "created_at": "2026-02-02T00:00:00Z",
            },
            {
                "table_name": "documents",
                "op_type": "upsert",
                "row_stable_id": "doc-contract-1",
                "row_payload": {
                    "id": 9001,
                    "session_id": "session-contract-1",
                    "doc_type": "checkpoint",
                    "seq": 0,
                    "title": "Contract Doc",
                    "stable_id": "doc-payload-mismatch",
                    "file_path": "/remote/contract/doc-1.md",
                    "file_hash": "abc123",
                    "size_bytes": 123,
                    "content_preview": "preview",
                    "source": "sync",
                    "indexed_at": "2026-02-02T00:00:00Z",
                },
                "op_index": 1,
                "created_at": "2026-02-02T00:00:00Z",
            },
            {
                "table_name": "sections",
                "op_type": "upsert",
                "row_stable_id": "section-contract-1",
                "row_payload": {
                    "id": 8001,
                    "document_id": 777777,
                    "document_stable_id": "doc-contract-1",
                    "section_name": "full",
                    "stable_id": "section-contract-1",
                    "content": "section content",
                },
                "op_index": 2,
                "created_at": "2026-02-02T00:00:00Z",
            },
            {
                "table_name": "knowledge_entries",
                "op_type": "upsert",
                "row_stable_id": "ke-contract-1",
                "row_payload": {
                    "id": 7001,
                    "session_id": "session-contract-1",
                    "document_id": 666666,
                    "document_stable_id": "doc-contract-1",
                    "category": "pattern",
                    "title": "Entry One",
                    "stable_id": "ke-contract-1",
                    "content": "entry one content",
                    "topic_key": "topic-one",
                },
                "op_index": 3,
                "created_at": "2026-02-02T00:00:00Z",
            },
            {
                "table_name": "knowledge_entries",
                "op_type": "upsert",
                "row_stable_id": "ke-contract-2",
                "row_payload": {
                    "id": 7002,
                    "session_id": "session-contract-1",
                    "category": "decision",
                    "title": "Entry Two",
                    "stable_id": "ke-contract-2",
                    "content": "entry two content",
                    "topic_key": "topic-two",
                },
                "op_index": 4,
                "created_at": "2026-02-02T00:00:00Z",
            },
            {
                "table_name": "knowledge_relations",
                "op_type": "upsert",
                "row_stable_id": "kr-contract-1",
                "row_payload": {
                    "id": 6001,
                    "source_id": 444444,
                    "target_id": 555555,
                    "source_stable_id": "ke-contract-1",
                    "target_stable_id": "ke-contract-2",
                    "relation_type": "related_to",
                    "stable_id": "kr-contract-1",
                    "confidence": 0.9,
                    "created_at": "2026-02-02T00:00:00Z",
                },
                "op_index": 5,
                "created_at": "2026-02-02T00:00:00Z",
            },
            {
                "table_name": "entity_relations",
                "op_type": "upsert",
                "row_stable_id": "er-contract-1",
                "row_payload": {
                    "id": 5001,
                    "subject": "sync",
                    "predicate": "supports",
                    "object": "portable-contract",
                    "stable_id": "er-contract-1",
                    "noted_at": "2026-02-02T00:00:00Z",
                    "session_id": "session-contract-1",
                },
                "op_index": 6,
                "created_at": "2026-02-02T00:00:00Z",
            },
            {
                "table_name": "search_feedback",
                "op_type": "upsert",
                "row_stable_id": "sf-contract-1",
                "row_payload": {
                    "id": 4001,
                    "query": "portable contract",
                    "result_id": "ke-contract-1",
                    "result_kind": "knowledge",
                    "verdict": 1,
                    "comment": "looks good",
                    "user_agent": "remote",
                    "created_at": "2026-02-02T00:00:00Z",
                    "origin_replica_id": "remote-contract",
                    "stable_id": "sf-contract-1",
                },
                "op_index": 7,
                "created_at": "2026-02-02T00:00:00Z",
            },
        ],
    },
    {
        "txn_id": "remote-contract-2",
        "replica_id": "remote-contract",
        "created_at": "2026-02-02T00:00:01Z",
        "committed_at": "2026-02-02T00:00:01Z",
        "status": "committed",
        "ops": [
            {
                "table_name": "sections",
                "op_type": "upsert",
                "row_stable_id": "section-missing-doc",
                "row_payload": {
                    "document_stable_id": "doc-missing",
                    "section_name": "full",
                    "stable_id": "section-missing-doc",
                    "content": "will fail",
                },
                "op_index": 0,
                "created_at": "2026-02-02T00:00:01Z",
            },
            {
                "table_name": "sessions",
                "op_type": "upsert",
                "row_stable_id": "session-after-fail-open",
                "row_payload": {
                    "id": "ignored-after-fail",
                    "path": "/repo/after-fail-open",
                    "summary": "still applied",
                    "total_checkpoints": 0,
                    "total_research": 0,
                    "total_files": 0,
                    "has_plan": 0,
                    "source": "sync",
                    "indexed_at": "2026-02-02T00:00:01Z",
                },
                "op_index": 2,
                "created_at": "2026-02-02T00:00:01Z",
            },
            {
                "table_name": "search_feedback",
                "op_type": "delete",
                "row_stable_id": "",
                "row_payload": {},
                "op_index": 1,
                "created_at": "2026-02-02T00:00:01Z",
            },
        ],
    },
]
try:
    db4 = sync_daemon.get_db(db4_path)
    pull_result4 = sync_daemon.pull_once(db4, base_url4, "local-runtime-contract", limit=20)
    db4.commit()

    session_contract = db4.execute("SELECT id, summary FROM sessions WHERE id='session-contract-1'").fetchone()
    session_after_fail = db4.execute("SELECT id, summary FROM sessions WHERE id='session-after-fail-open'").fetchone()
    ignored_session = db4.execute("SELECT id FROM sessions WHERE id='remote-session-id-ignored'").fetchone()
    doc_row = db4.execute("SELECT id, stable_id FROM documents WHERE stable_id='doc-contract-1'").fetchone()
    doc_payload_mismatch = db4.execute("SELECT id FROM documents WHERE stable_id='doc-payload-mismatch'").fetchone()
    section_row = db4.execute("SELECT document_id FROM sections WHERE stable_id='section-contract-1'").fetchone()
    ke_one = db4.execute("SELECT id, document_id FROM knowledge_entries WHERE stable_id='ke-contract-1'").fetchone()
    ke_two = db4.execute("SELECT id FROM knowledge_entries WHERE stable_id='ke-contract-2'").fetchone()
    kr_row = db4.execute(
        "SELECT source_id, target_id FROM knowledge_relations WHERE stable_id='kr-contract-1'"
    ).fetchone()
    er_row = db4.execute(
        "SELECT subject, predicate, object FROM entity_relations WHERE stable_id='er-contract-1'"
    ).fetchone()
    sf_row = db4.execute(
        "SELECT id, stable_id, origin_replica_id FROM search_feedback WHERE stable_id='sf-contract-1'"
    ).fetchone()
    sf_empty_row = db4.execute("SELECT id FROM search_feedback WHERE stable_id=''").fetchone()
    ke_fts_row = (
        db4.execute(
            "SELECT rowid, title FROM ke_fts WHERE rowid = ?",
            (ke_one[0],),
        ).fetchone()
        if ke_one is not None
        else None
    )
    knowledge_fts_row = (
        db4.execute(
            "SELECT document_id, content FROM knowledge_fts WHERE document_id = ?",
            (doc_row[0],),
        ).fetchone()
        if doc_row is not None
        else None
    )
    cursor4 = db4.execute("SELECT last_txn_id FROM sync_cursors WHERE replica_id='local-runtime-contract'").fetchone()
    failure4 = db4.execute(
        """
        SELECT table_name, row_stable_id
        FROM sync_failures
        WHERE error_code='remote_apply_op' AND row_stable_id='section-missing-doc'
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()

    test("real-schema pull applies both txns", pull_result4["applied"] == 2, str(pull_result4))
    test(
        "sessions use row_stable_id instead of remote id",
        session_contract is not None and ignored_session is None,
        str(session_contract),
    )
    test("documents ignore remote surrogate id", doc_row is not None and doc_row[0] != 9001, str(doc_row))
    test(
        "documents keep envelope row_stable_id authoritative over payload stable_id",
        doc_row is not None and doc_row[1] == "doc-contract-1" and doc_payload_mismatch is None,
        str(doc_row),
    )
    test(
        "sections resolve document_stable_id to local document_id",
        section_row is not None and doc_row is not None and section_row[0] == doc_row[0],
        str(section_row),
    )
    test(
        "knowledge_entries resolve optional document_stable_id",
        ke_one is not None and doc_row is not None and ke_one[1] == doc_row[0],
        str(ke_one),
    )
    test(
        "knowledge_relations resolve source/target local ids",
        kr_row is not None
        and ke_one is not None
        and ke_two is not None
        and kr_row[0] == ke_one[0]
        and kr_row[1] == ke_two[0],
        str(kr_row),
    )
    test("entity_relations apply by stable_id contract", er_row is not None and er_row[0] == "sync", str(er_row))
    test(
        "search_feedback applies without trusting remote id",
        sf_row is not None and sf_row[0] != 4001 and sf_row[1] == "sf-contract-1",
        str(sf_row),
    )
    test("empty row_stable_id delete is ignored", sf_empty_row is not None, str(sf_empty_row))
    test(
        "pull refreshes ke_fts for synced knowledge entries",
        ke_fts_row is not None and ke_fts_row[0] == ke_one[0],
        str(ke_fts_row),
    )
    test(
        "pull refreshes knowledge_fts for synced sections",
        knowledge_fts_row is not None and "section content" in knowledge_fts_row[1],
        str(knowledge_fts_row),
    )
    test(
        "per-op failure is fail-open for remaining ops",
        session_after_fail is not None and session_after_fail[1] == "still applied",
        str(session_after_fail),
    )
    test(
        "pull cursor advances after partial-op failures",
        cursor4 is not None and cursor4[0] == "remote-contract-2",
        str(cursor4),
    )
    test(
        "failed unresolved helper reference is recorded",
        failure4 is not None and failure4[0] == "sections",
        str(failure4),
    )

    db4.close()
finally:
    server4.shutdown()
    server4.server_close()
    thread4.join(timeout=2)

# regression: remote delete ops must clear stale FTS rows even after canonical rows are gone.
db_delete_path = ARTIFACT_DIR / "knowledge-delete-fts.db"
make_real_schema_db(db_delete_path)
db_delete = sqlite3.connect(str(db_delete_path))
db_delete.row_factory = sqlite3.Row
sync_daemon.ensure_sync_foundation(db_delete)
db_delete.commit()
db_delete.close()

server_delete, thread_delete, gateway_state_delete, base_url_delete = with_gateway()
try:
    gateway_state_delete.pull_txns = [
        {
            "txn_id": "remote-delete-seed-1",
            "replica_id": "remote-delete",
            "created_at": "2026-02-03T00:00:00Z",
            "committed_at": "2026-02-03T00:00:00Z",
            "status": "committed",
            "ops": [
                {
                    "table_name": "sessions",
                    "op_type": "upsert",
                    "row_stable_id": "session-delete-1",
                    "row_payload": {"id": "session-delete-1", "path": "/repo/delete-seed", "summary": "seed"},
                    "op_index": 0,
                    "created_at": "2026-02-03T00:00:00Z",
                },
                {
                    "table_name": "documents",
                    "op_type": "upsert",
                    "row_stable_id": "doc-delete-1",
                    "row_payload": {
                        "session_id": "session-delete-1",
                        "doc_type": "checkpoint",
                        "seq": 0,
                        "title": "Delete Doc",
                        "stable_id": "doc-delete-1",
                        "file_path": "/repo/delete/doc.md",
                        "file_hash": "hash-delete",
                        "size_bytes": 10,
                        "content_preview": "doc",
                        "source": "sync",
                        "indexed_at": "2026-02-03T00:00:00Z",
                    },
                    "op_index": 1,
                    "created_at": "2026-02-03T00:00:00Z",
                },
                {
                    "table_name": "sections",
                    "op_type": "upsert",
                    "row_stable_id": "section-delete-1",
                    "row_payload": {
                        "document_stable_id": "doc-delete-1",
                        "section_name": "full",
                        "stable_id": "section-delete-1",
                        "content": "seed section",
                    },
                    "op_index": 2,
                    "created_at": "2026-02-03T00:00:00Z",
                },
                {
                    "table_name": "knowledge_entries",
                    "op_type": "upsert",
                    "row_stable_id": "ke-delete-1",
                    "row_payload": {
                        "session_id": "session-delete-1",
                        "document_stable_id": "doc-delete-1",
                        "category": "pattern",
                        "title": "Delete Entry",
                        "stable_id": "ke-delete-1",
                        "content": "seed entry",
                        "topic_key": "delete-topic",
                    },
                    "op_index": 3,
                    "created_at": "2026-02-03T00:00:00Z",
                },
            ],
        }
    ]
    db_delete = sync_daemon.get_db(db_delete_path)
    seeded_pull = sync_daemon.pull_once(db_delete, base_url_delete, "local-delete-fts", limit=20)
    db_delete.commit()

    seeded_doc = db_delete.execute("SELECT id FROM documents WHERE stable_id='doc-delete-1'").fetchone()
    seeded_entry = db_delete.execute("SELECT id FROM knowledge_entries WHERE stable_id='ke-delete-1'").fetchone()
    seeded_knowledge_fts = (
        db_delete.execute(
            "SELECT document_id FROM knowledge_fts WHERE document_id=?",
            (seeded_doc[0],),
        ).fetchone()
        if seeded_doc is not None
        else None
    )
    seeded_ke_fts = (
        db_delete.execute(
            "SELECT rowid FROM ke_fts WHERE rowid=?",
            (seeded_entry[0],),
        ).fetchone()
        if seeded_entry is not None
        else None
    )
    test("delete regression seed pull applied", seeded_pull["applied"] == 1, str(seeded_pull))
    test(
        "delete regression seed creates knowledge_fts row", seeded_knowledge_fts is not None, str(seeded_knowledge_fts)
    )
    test("delete regression seed creates ke_fts row", seeded_ke_fts is not None, str(seeded_ke_fts))

    gateway_state_delete.pull_txns = [
        {
            "txn_id": "remote-delete-seed-2",
            "replica_id": "remote-delete",
            "created_at": "2026-02-03T00:00:01Z",
            "committed_at": "2026-02-03T00:00:01Z",
            "status": "committed",
            "ops": [
                {
                    "table_name": "documents",
                    "op_type": "delete",
                    "row_stable_id": "doc-delete-1",
                    "row_payload": {},
                    "op_index": 0,
                    "created_at": "2026-02-03T00:00:01Z",
                },
                {
                    "table_name": "knowledge_entries",
                    "op_type": "delete",
                    "row_stable_id": "ke-delete-1",
                    "row_payload": {},
                    "op_index": 1,
                    "created_at": "2026-02-03T00:00:01Z",
                },
            ],
        }
    ]
    delete_pull = sync_daemon.pull_once(db_delete, base_url_delete, "local-delete-fts", limit=20)
    db_delete.commit()

    deleted_doc = db_delete.execute("SELECT id FROM documents WHERE stable_id='doc-delete-1'").fetchone()
    deleted_entry = db_delete.execute("SELECT id FROM knowledge_entries WHERE stable_id='ke-delete-1'").fetchone()
    stale_knowledge_fts = (
        db_delete.execute(
            "SELECT document_id FROM knowledge_fts WHERE document_id=?",
            (seeded_doc[0],),
        ).fetchone()
        if seeded_doc is not None
        else None
    )
    stale_ke_fts = (
        db_delete.execute(
            "SELECT rowid FROM ke_fts WHERE rowid=?",
            (seeded_entry[0],),
        ).fetchone()
        if seeded_entry is not None
        else None
    )
    db_delete.close()

    test("delete regression pull applied", delete_pull["applied"] == 1, str(delete_pull))
    test("remote document delete removes canonical document row", deleted_doc is None, str(deleted_doc))
    test("remote knowledge entry delete removes canonical entry row", deleted_entry is None, str(deleted_entry))
    test(
        "remote document delete removes stale knowledge_fts row", stale_knowledge_fts is None, str(stale_knowledge_fts)
    )
    test("remote knowledge entry delete removes stale ke_fts row", stale_ke_fts is None, str(stale_ke_fts))
finally:
    server_delete.shutdown()
    server_delete.server_close()
    thread_delete.join(timeout=2)

# Hook marker consumption should trigger best-effort flush cycle in --once mode.
db3_path = ARTIFACT_DIR / "knowledge-flush.db"
make_db(db3_path)
db3 = sqlite3.connect(str(db3_path))
db3.row_factory = sqlite3.Row
sync_daemon.ensure_sync_foundation(db3)
db3.execute("INSERT OR REPLACE INTO sync_state (key, value) VALUES ('local_replica_id', 'local-test-flush')")
db3.execute(
    "INSERT INTO sync_txns (txn_id, replica_id, status, created_at, committed_at) VALUES (?, ?, 'pending', ?, '')",
    (
        "local-txn-flush",
        "local-test-flush",
        datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    ),
)
db3.execute(
    """
    INSERT INTO sync_ops (txn_id, table_name, op_type, row_stable_id, row_payload, op_index, created_at)
    VALUES (?, 'sessions', 'upsert', 'session-local-flush', ?, 0, ?)
    """,
    (
        "local-txn-flush",
        json.dumps(
            {
                "id": "session-local-flush",
                "path": "/repo/local-flush",
                "summary": "local flush row",
                "total_checkpoints": 1,
                "total_research": 0,
                "total_files": 0,
                "has_plan": 1,
                "source": "copilot",
                "indexed_at": "2026-03-01T00:00:00Z",
            }
        ),
        datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    ),
)
db3.commit()
db3.close()

server3, thread3, gateway_state3, base_url3 = with_gateway()
gateway_state3.pull_txns = [
    {
        "txn_id": "remote-flush-1",
        "replica_id": "remote-c",
        "created_at": "2026-03-01T00:00:00Z",
        "committed_at": "2026-03-01T00:00:00Z",
        "status": "committed",
        "ops": [
            {
                "table_name": "sessions",
                "op_type": "upsert",
                "row_stable_id": "session-remote-flush-1",
                "row_payload": {
                    "id": "session-remote-flush-1",
                    "path": "/repo/remote-flush",
                    "summary": "remote flush row",
                    "total_checkpoints": 1,
                    "total_research": 0,
                    "total_files": 0,
                    "has_plan": 0,
                    "source": "sync",
                    "indexed_at": "2026-03-01T00:00:00Z",
                },
                "op_index": 0,
                "created_at": "2026-03-01T00:00:00Z",
            }
        ],
    }
]
try:
    sync_daemon.DB_PATH = db3_path
    sync_daemon.SYNC_CONFIG_PATH = ARTIFACT_DIR / "sync-config-flush.json"
    sync_daemon.MARKERS_DIR = ARTIFACT_DIR / "markers"
    sync_daemon.SYNC_NUDGE_MARKER = sync_daemon.MARKERS_DIR / "sync-nudge.json"
    sync_daemon.SYNC_FLUSH_MARKER = sync_daemon.MARKERS_DIR / "sync-flush.json"
    sync_daemon.MARKERS_DIR.mkdir(parents=True, exist_ok=True)
    sync_daemon.SYNC_FLUSH_MARKER.write_text(
        json.dumps({"event": "sessionEnd", "session_id": "s1", "ts": "2026-03-01T00:00:00Z"}),
        encoding="utf-8",
    )
    sync_daemon.SYNC_CONFIG_PATH.write_text(json.dumps({"connection_string": base_url3}), encoding="utf-8")
    if os.name != "nt":
        os.chmod(sync_daemon.SYNC_CONFIG_PATH, 0o600)
    code = sync_daemon.run_loop(once=True, pull_only=True, limit=10)
    test("sync-daemon once mode exits success with flush marker", code == 0, str(code))
    test("sync-flush marker consumed", not sync_daemon.SYNC_FLUSH_MARKER.exists())
    test("flush marker forces push even in pull-only mode", len(gateway_state3.pushed_payloads) == 1)
    db3 = sqlite3.connect(str(db3_path))
    remote_flush_row = db3.execute("SELECT summary FROM sessions WHERE id='session-remote-flush-1'").fetchone()
    local_txn_state = db3.execute("SELECT status FROM sync_txns WHERE txn_id='local-txn-flush'").fetchone()
    db3.close()
    test(
        "flush marker still performs pull",
        remote_flush_row is not None and remote_flush_row[0] == "remote flush row",
        str(remote_flush_row),
    )
    test(
        "flush marker push commits pending txn",
        local_txn_state is not None and local_txn_state[0] == "committed",
        str(local_txn_state),
    )
finally:
    server3.shutdown()
    server3.server_close()
    thread3.join(timeout=2)

# Pull pagination should continue within one cycle while gateway reports has_more.
db5_path = ARTIFACT_DIR / "knowledge-pagination.db"
make_db(db5_path)
db5 = sqlite3.connect(str(db5_path))
db5.row_factory = sqlite3.Row
sync_daemon.ensure_sync_foundation(db5)
db5.commit()
db5.close()

pages = [
    {
        "txns": [
            {
                "txn_id": "remote-page-1",
                "replica_id": "remote-page",
                "created_at": "2026-03-02T00:00:00Z",
                "committed_at": "2026-03-02T00:00:00Z",
                "status": "committed",
                "ops": [
                    {
                        "table_name": "sessions",
                        "op_type": "upsert",
                        "row_stable_id": "session-page-1",
                        "row_payload": {"id": "session-page-1", "path": "/repo/page-1", "summary": "p1"},
                        "op_index": 0,
                        "created_at": "2026-03-02T00:00:00Z",
                    }
                ],
            }
        ],
        "next_after": "remote-page-1",
        "has_more": True,
    },
    {
        "txns": [
            {
                "txn_id": "remote-page-2",
                "replica_id": "remote-page",
                "created_at": "2026-03-02T00:00:01Z",
                "committed_at": "2026-03-02T00:00:01Z",
                "status": "committed",
                "ops": [
                    {
                        "table_name": "sessions",
                        "op_type": "upsert",
                        "row_stable_id": "session-page-2",
                        "row_payload": {"id": "session-page-2", "path": "/repo/page-2", "summary": "p2"},
                        "op_index": 0,
                        "created_at": "2026-03-02T00:00:01Z",
                    }
                ],
            }
        ],
        "next_after": "remote-page-2",
        "has_more": False,
    },
]
request_calls = []
original_request_json = sync_daemon._request_json
try:

    def _paged_request(url: str, method: str = "GET", payload: dict | None = None, timeout: int = 10):
        request_calls.append(url)
        if not pages:
            return {"txns": [], "next_after": "remote-page-2", "has_more": False}
        return pages.pop(0)

    sync_daemon._request_json = _paged_request
    db5 = sync_daemon.get_db(db5_path)
    paged_pull = sync_daemon.pull_once(db5, "http://sync.test", "local-pagination", limit=1)
    db5.commit()
    page_rows = db5.execute("SELECT COUNT(*) FROM sessions WHERE id IN ('session-page-1','session-page-2')").fetchone()[
        0
    ]
    page_cursor = db5.execute("SELECT last_txn_id FROM sync_cursors WHERE replica_id='local-pagination'").fetchone()
    db5.close()
    test("pull pagination fetches multiple pages in one cycle", len(request_calls) >= 2, str(request_calls))
    test("pull pagination applies all paged txns", paged_pull["applied"] == 2 and page_rows == 2, str(paged_pull))
    test(
        "pull pagination advances cursor to last page",
        page_cursor is not None and page_cursor[0] == "remote-page-2",
        str(page_cursor),
    )
finally:
    sync_daemon._request_json = original_request_json

# Backlog-aware limit scales up for relation-heavy queues.
db6_path = ARTIFACT_DIR / "knowledge-backlog.db"
make_db(db6_path)
db6 = sqlite3.connect(str(db6_path))
db6.row_factory = sqlite3.Row
sync_daemon.ensure_sync_foundation(db6)
for i in range(0, 260):
    txn_id = f"pending-rel-{i}"
    created_at = f"2026-03-03T00:00:{i % 60:02d}Z"
    db6.execute(
        "INSERT INTO sync_txns (txn_id, replica_id, status, created_at, committed_at) VALUES (?, 'local-backlog', 'pending', ?, '')",
        (txn_id, created_at),
    )
    db6.execute(
        """
        INSERT INTO sync_ops (txn_id, table_name, op_type, row_stable_id, row_payload, op_index, created_at)
        VALUES (?, 'knowledge_relations', 'upsert', ?, '{}', 0, ?)
        """,
        (txn_id, f"kr-{i}", created_at),
    )
db6.commit()
scaled_limit = sync_daemon._effective_sync_limit(db6, 10)
db6.close()
test("relation-heavy backlog increases per-cycle sync limit", scaled_limit >= 100, str(scaled_limit))

# Pending sync queue compaction should coalesce repeated local writes before network health checks.
db7_path = ARTIFACT_DIR / "knowledge-queue-compaction.db"
make_db(db7_path)
db7 = sqlite3.connect(str(db7_path))
db7.row_factory = sqlite3.Row
sync_daemon.ensure_sync_foundation(db7)
for i in range(0, 8):
    txn_id = f"compact-session-{i}"
    created_at = f"2026-03-04T00:00:{i:02d}Z"
    db7.execute(
        "INSERT INTO sync_txns (txn_id, replica_id, status, created_at, committed_at) VALUES (?, 'local-compact', 'pending', ?, '')",
        (txn_id, created_at),
    )
    db7.execute(
        """
        INSERT INTO sync_ops (txn_id, table_name, op_type, row_stable_id, row_payload, op_index, created_at)
        VALUES (?, 'sessions', 'upsert', 'session-compact', ?, 0, ?)
        """,
        (txn_id, json.dumps({"id": "session-compact", "summary": f"v{i}"}), created_at),
    )
for i in range(0, 3):
    txn_id = f"compact-relation-{i}"
    created_at = f"2026-03-04T00:01:{i:02d}Z"
    db7.execute(
        "INSERT INTO sync_txns (txn_id, replica_id, status, created_at, committed_at) VALUES (?, 'local-compact', 'pending', ?, '')",
        (txn_id, created_at),
    )
    db7.execute(
        """
        INSERT INTO sync_ops (txn_id, table_name, op_type, row_stable_id, row_payload, op_index, created_at)
        VALUES (?, 'knowledge_relations', 'upsert', 'rel-compact', ?, 0, ?)
        """,
        (txn_id, json.dumps({"stable_id": "rel-compact", "confidence": i}), created_at),
    )
db7.commit()
compaction = sync_daemon.compact_pending_sync_queue(db7, "local-compact", force=True)
remaining_txns = db7.execute(
    "SELECT COUNT(*) FROM sync_txns WHERE status='pending' AND replica_id='local-compact'"
).fetchone()[0]
remaining_ops = db7.execute(
    "SELECT COUNT(*) FROM sync_ops o JOIN sync_txns t ON t.txn_id=o.txn_id WHERE t.status='pending' AND t.replica_id='local-compact'"
).fetchone()[0]
latest_session_payload = json.loads(
    db7.execute("SELECT row_payload FROM sync_ops WHERE table_name='sessions' AND row_stable_id='session-compact'").fetchone()[
        0
    ]
)
latest_relation_payload = json.loads(
    db7.execute(
        "SELECT row_payload FROM sync_ops WHERE table_name='knowledge_relations' AND row_stable_id='rel-compact'"
    ).fetchone()[0]
)
db7.close()
test(
    "sync queue compaction coalesces duplicate pending rows",
    compaction["compacted"] is True and remaining_txns == 1 and remaining_ops == 2,
    f"compaction={compaction} txns={remaining_txns} ops={remaining_ops}",
)
test(
    "sync queue compaction keeps latest row payload",
    latest_session_payload.get("summary") == "v7" and latest_relation_payload.get("confidence") == 2,
    f"session={latest_session_payload} relation={latest_relation_payload}",
)

db8_path = ARTIFACT_DIR / "knowledge-queue-compaction-rollback.db"
make_db(db8_path)
db8 = sqlite3.connect(str(db8_path))
db8.row_factory = sqlite3.Row
sync_daemon.ensure_sync_foundation(db8)
for i in range(0, 3):
    txn_id = f"rollback-session-{i}"
    created_at = f"2026-03-04T00:02:{i:02d}Z"
    db8.execute(
        "INSERT INTO sync_txns (txn_id, replica_id, status, created_at, committed_at) VALUES (?, 'local-rollback', 'pending', ?, '')",
        (txn_id, created_at),
    )
    db8.execute(
        """
        INSERT INTO sync_ops (txn_id, table_name, op_type, row_stable_id, row_payload, op_index, created_at)
        VALUES (?, 'sessions', 'upsert', ?, ?, 0, ?)
        """,
        (txn_id, f"session-rollback-{i}", json.dumps({"id": f"session-rollback-{i}"}), created_at),
    )
db8.execute(
    "INSERT INTO sync_txns (txn_id, replica_id, status, created_at, committed_at) VALUES ('collision-txn', 'local-rollback', 'committed', '2026-03-03T00:00:00Z', '2026-03-03T00:00:01Z')"
)
db8.commit()
original_stable_sha256 = sync_daemon._stable_sha256
rollback_error = None
try:
    sync_daemon._stable_sha256 = lambda *parts: "collision-txn"
    try:
        sync_daemon.compact_pending_sync_queue(db8, "local-rollback", force=True)
    except sqlite3.DatabaseError as exc:
        rollback_error = exc
finally:
    sync_daemon._stable_sha256 = original_stable_sha256
rollback_pending_txns = db8.execute(
    "SELECT COUNT(*) FROM sync_txns WHERE status='pending' AND replica_id='local-rollback'"
).fetchone()[0]
rollback_pending_ops = db8.execute(
    "SELECT COUNT(*) FROM sync_ops o JOIN sync_txns t ON t.txn_id=o.txn_id WHERE t.status='pending' AND t.replica_id='local-rollback'"
).fetchone()[0]
temp_tables_after_rollback = db8.execute(
    "SELECT COUNT(*) FROM sqlite_temp_master WHERE type='table' AND name LIKE 'sync_compact_%'"
).fetchone()[0]
db8.close()
test(
    "sync queue compaction rolls back failed rebuild",
    rollback_error is not None and rollback_pending_txns == 3 and rollback_pending_ops == 3,
    f"error={rollback_error} txns={rollback_pending_txns} ops={rollback_pending_ops}",
)
test(
    "sync queue compaction cleans temp tables after rollback",
    temp_tables_after_rollback == 0,
    str(temp_tables_after_rollback),
)

print("\n🔧 sync-knowledge runtime status summary")
db = sqlite3.connect(str(db_path))
status = sync_knowledge._sync_runtime_status(db)

test("sync-knowledge runtime reflects configured gateway", status["configured"] is True)
test("sync-knowledge runtime pending count is zero after push", status["pending_txns"] == 0, str(status))
db.close()

# ---------------------------------------------------------------------------
# knowledge_entries confidence MAX merge semantics
# ---------------------------------------------------------------------------
print("\nknowledge_entries confidence MAX merge semantics")
print("-" * 53)


def _make_confidence_merge_db(path: Path) -> sqlite3.Connection:
    """Create a minimal knowledge DB at path with sessions + knowledge_entries."""
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            path TEXT, summary TEXT, total_checkpoints INTEGER DEFAULT 0,
            total_research INTEGER DEFAULT 0, total_files INTEGER DEFAULT 0,
            has_plan INTEGER DEFAULT 0, source TEXT DEFAULT 'copilot',
            indexed_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            document_id INTEGER,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            tags TEXT DEFAULT '',
            confidence REAL NOT NULL DEFAULT 0.5,
            occurrence_count INTEGER DEFAULT 1,
            first_seen TEXT DEFAULT (date('now')),
            last_seen TEXT DEFAULT (date('now')),
            source TEXT DEFAULT 'copilot',
            UNIQUE(category, title, session_id)
        )
    """)
    conn.commit()
    return conn


_merge_dir = ARTIFACT_DIR / "confidence_merge"
_merge_dir.mkdir(parents=True, exist_ok=True)
_target_path = _merge_dir / "target.db"
_source_path = _merge_dir / "source.db"

_target_db = _make_confidence_merge_db(_target_path)
_source_db = _make_confidence_merge_db(_source_path)

# Seed target with a low-confidence pattern entry
_target_db.execute("INSERT INTO sessions (id) VALUES ('s1')")
_target_db.execute(
    "INSERT INTO knowledge_entries (session_id, category, title, confidence) VALUES ('s1', 'pattern', 'use context manager', 0.5)"
)
_target_db.commit()

# Seed source with same entry but higher confidence (post-backfill)
_source_db.execute("INSERT INTO sessions (id) VALUES ('s1')")
_source_db.execute(
    "INSERT INTO knowledge_entries (session_id, category, title, confidence) VALUES ('s1', 'pattern', 'use context manager', 0.8)"
)
_source_db.commit()
_source_db.close()

# Run sync
_target_db.close()
_target_db = sqlite3.connect(str(_target_path))
_sync_result = sync_knowledge.sync_from_source(_target_db, _source_path)

_merged_conf = _target_db.execute(
    "SELECT confidence FROM knowledge_entries WHERE title='use context manager' AND session_id='s1'"
).fetchone()
_target_db.close()

test(
    "knowledge_entries confidence MAX merge: sync summary counts update-only merges",
    isinstance(_sync_result, dict) and _sync_result.get("knowledge_entries") == 1,
    str(_sync_result),
)
test(
    "knowledge_entries confidence MAX merge: target updated to source's higher confidence",
    _merged_conf is not None and _merged_conf[0] >= 0.8,
    f"confidence={_merged_conf[0] if _merged_conf else 'missing'}",
)

# Also verify that sync does NOT downgrade if source has lower confidence
_target2_path = _merge_dir / "target2.db"
_source2_path = _merge_dir / "source2.db"
_target2_db = _make_confidence_merge_db(_target2_path)
_source2_db = _make_confidence_merge_db(_source2_path)
_target2_db.execute("INSERT INTO sessions (id) VALUES ('s1')")
_target2_db.execute(
    "INSERT INTO knowledge_entries (session_id, category, title, confidence) VALUES ('s1', 'pattern', 'validate early', 0.85)"
)
_target2_db.commit()
_source2_db.execute("INSERT INTO sessions (id) VALUES ('s1')")
_source2_db.execute(
    "INSERT INTO knowledge_entries (session_id, category, title, confidence) VALUES ('s1', 'pattern', 'validate early', 0.6)"
)
_source2_db.commit()
_source2_db.close()
_target2_db.close()
_target2_db = sqlite3.connect(str(_target2_path))
sync_knowledge.sync_from_source(_target2_db, _source2_path)
_keep_conf = _target2_db.execute(
    "SELECT confidence FROM knowledge_entries WHERE title='validate early' AND session_id='s1'"
).fetchone()
_target2_db.close()
test(
    "knowledge_entries confidence MAX merge: target keeps higher confidence when source is lower",
    _keep_conf is not None and _keep_conf[0] >= 0.85,
    f"confidence={_keep_conf[0] if _keep_conf else 'missing'}",
)

print(f"\nResult: {PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)

# ---------------------------------------------------------------------------
# Native sk runtime restart coverage
# ---------------------------------------------------------------------------
print("\n── Native sk runtime restart coverage ──────────────────────────────────")

auto_update = load_module("auto_update_test", "auto-update-tools.py")


def test_sk_binary_path_defined():
    """auto-update-tools.py must export _sk_binary_path for native restart routing."""
    test(
        "_sk_binary_path is defined in auto-update-tools",
        hasattr(auto_update, "_sk_binary_path"),
        "auto-update-tools.py missing _sk_binary_path(); native sk restart routing broken",
    )


def test_sk_binary_path_returns_none_when_absent():
    """_sk_binary_path must return None when ~/.copilot/bin has no sk binary."""
    import shutil as _shutil
    import tempfile as _tempfile

    tmp_home = Path(_tempfile.mkdtemp(prefix="sk-path-test-"))
    try:
        original_home = auto_update.HOME
        auto_update.HOME = tmp_home
        result = auto_update._sk_binary_path()
        test(
            "_sk_binary_path returns None when no sk binary installed",
            result is None,
            f"expected None, got {result!r}",
        )
    finally:
        auto_update.HOME = original_home
        _shutil.rmtree(tmp_home, ignore_errors=True)


def test_sk_binary_path_returns_native_when_present():
    """_sk_binary_path returns native binary path (sk.exe / sk-native) when present."""
    import shutil as _shutil
    import tempfile as _tempfile
    import platform as _platform

    tmp_home = Path(_tempfile.mkdtemp(prefix="sk-native-test-"))
    try:
        bin_dir = tmp_home / ".copilot" / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        if _platform.system() == "Windows":
            native_name = "sk.exe"
        else:
            native_name = "sk-native"
        native_bin = bin_dir / native_name
        native_bin.write_text("#!/bin/sh\necho sk", encoding="utf-8")

        original_home = auto_update.HOME
        auto_update.HOME = tmp_home
        result = auto_update._sk_binary_path()
        test(
            "_sk_binary_path returns native binary path when present",
            result is not None and result.name == native_name,
            f"expected {native_name!r}, got {result!r}",
        )
    finally:
        auto_update.HOME = original_home
        _shutil.rmtree(tmp_home, ignore_errors=True)


def test_sk_binary_path_falls_back_to_shim():
    """_sk_binary_path falls back to Python shim when native binary is absent."""
    import shutil as _shutil
    import tempfile as _tempfile
    import platform as _platform

    tmp_home = Path(_tempfile.mkdtemp(prefix="sk-shim-test-"))
    try:
        bin_dir = tmp_home / ".copilot" / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        if _platform.system() == "Windows":
            shim_name = "sk.cmd"
        else:
            shim_name = "sk"
        shim = bin_dir / shim_name
        shim.write_text(
            "@echo off\npython sk.py %*" if _platform.system() == "Windows" else '#!/bin/sh\npython3 sk.py "$@"',
            encoding="utf-8",
        )

        original_home = auto_update.HOME
        auto_update.HOME = tmp_home
        result = auto_update._sk_binary_path()
        test(
            "_sk_binary_path falls back to Python shim when native binary absent",
            result is not None and result.name == shim_name,
            f"expected {shim_name!r}, got {result!r}",
        )
    finally:
        auto_update.HOME = original_home
        _shutil.rmtree(tmp_home, ignore_errors=True)


def test_sk_binary_path_prefers_native_over_shim():
    """_sk_binary_path must prefer native Rust binary over Python shim."""
    import shutil as _shutil
    import tempfile as _tempfile
    import platform as _platform

    tmp_home = Path(_tempfile.mkdtemp(prefix="sk-prefer-test-"))
    try:
        bin_dir = tmp_home / ".copilot" / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        if _platform.system() == "Windows":
            native_name, shim_name = "sk.exe", "sk.cmd"
        else:
            native_name, shim_name = "sk-native", "sk"
        (bin_dir / native_name).write_text("native", encoding="utf-8")
        (bin_dir / shim_name).write_text("shim", encoding="utf-8")

        original_home = auto_update.HOME
        auto_update.HOME = tmp_home
        result = auto_update._sk_binary_path()
        test(
            "_sk_binary_path prefers native binary over Python shim",
            result is not None and result.name == native_name,
            f"expected {native_name!r}, got {result!r}",
        )
    finally:
        auto_update.HOME = original_home
        _shutil.rmtree(tmp_home, ignore_errors=True)


def test_restart_manual_source_references_sk_binary_path():
    """_restart_manual() source must call _sk_binary_path() for native watch routing."""
    import inspect as _inspect

    source = _inspect.getsource(auto_update._restart_manual)
    test(
        "_restart_manual calls _sk_binary_path() for native watch routing",
        "_sk_binary_path()" in source,
        "_restart_manual must use _sk_binary_path() to prefer sk binary over direct python3 spawn",
    )
    test(
        "_restart_manual spawns 'watch' subcommand via sk binary",
        '"watch"' in source or "'watch'" in source,
        "_restart_manual should pass 'watch' as argument to the sk binary",
    )


test_sk_binary_path_defined()
test_sk_binary_path_returns_none_when_absent()
test_sk_binary_path_returns_native_when_present()
test_sk_binary_path_falls_back_to_shim()
test_sk_binary_path_prefers_native_over_shim()
test_restart_manual_source_references_sk_binary_path()

# ---------------------------------------------------------------------------
# Sync FTS refresh regression: Python fallback path + current native-sync state
# ---------------------------------------------------------------------------
# Current sync runtime facts:
#   - Native sync engine landed under 'native-sync' Cargo feature.
#   - DB layer (schema, replica ID, collect txns, apply ops, mark committed,
#     repair, failures) is ALWAYS compiled.
#   - HTTP engine (health+push+pull) is gated behind 'native-sync' feature.
#   - Replica ID seed omits MAC address in Rust; existing Python-generated IDs
#     are reused from the DB, so hybrid installs stay consistent.
#
# Current FTS refresh state:
#   - FTS blocker CLOSED: 'native-sync' feature now refreshes knowledge_fts
#     and ke_fts after pull natively in Rust.
#   - Python sync-daemon.py STILL defines the FTS refresh helpers; they remain
#     as the authoritative fallback for the Python sk.py shim / no-binary path.
#
# Current feature-default state:
#   - 'native-sync' is now in the DEFAULT Cargo feature set:
#     default = ["native-embed", "native-sync"]
#   - The standard compiled 'sk' binary routes 'sk sync run' natively (including
#     push, pull, and FTS refresh) without any --features flag.
#   - The Python sk.py shim and installs without a compiled binary STILL delegate
#     to sync-daemon.py --once; sync-daemon.py must NOT be removed.
#
# Current update:
#   - Native Copilot watch indexer (sk-rust/src/index/session.rs) now enqueues
#     sync_txns/sync_ops rows via enqueue_doc_sync_op_fail_open() after each
#     indexed document. This is state 1 (native implementation), fail-open.
#   - A native local-only sessions_fts writer exists for the non-JSONL
#     Copilot watch path, closing the prior confirmed gap.
#
# These tests guard that the Python fallback path remains intact and that
# USAGE.md accurately reflects the current hybrid state.
# ---------------------------------------------------------------------------
print("\n── Sync FTS refresh regression (Python fallback + native watch parity) ───")


def test_sync_fts_refresh_functions_exist():
    """Python sync-daemon.py must still define FTS refresh helpers as the Python shim / no-binary fallback.

    Note: native-sync is now in the default Cargo feature set, so the compiled sk binary
    routes sync natively. Python sync-daemon.py remains the authoritative fallback for:
    - The Python sk.py shim (always routes to sync-daemon.py)
    - Installs without a compiled binary
    """
    test(
        "sync-daemon._refresh_knowledge_fts_for_documents defined (Python fallback for default build)",
        hasattr(sync_daemon, "_refresh_knowledge_fts_for_documents"),
        "sync-daemon.py missing _refresh_knowledge_fts_for_documents — FTS refresh fallback broken",
    )
    test(
        "sync-daemon._refresh_ke_fts_for_entries defined (Python fallback for default build)",
        hasattr(sync_daemon, "_refresh_ke_fts_for_entries"),
        "sync-daemon.py missing _refresh_ke_fts_for_entries — ke_fts refresh fallback broken",
    )


def test_fts_refresh_works_for_documents():
    """Python _refresh_knowledge_fts_for_documents must update knowledge_fts rows after pull.

    Note: native-sync is now in default Cargo features, so the compiled sk binary handles
    FTS refresh natively. This test guards the Python fallback path used by the Python sk.py shim
    and installs without a compiled binary.
    """
    fts_path = ARTIFACT_DIR / "fts-refresh.db"
    make_real_schema_db(fts_path)
    db = sync_daemon.get_db(fts_path)
    sync_daemon.ensure_sync_foundation(db)

    # Insert a document and section (simulating what a pull would create).
    db.execute(
        """
        INSERT INTO sessions (id, path, summary, source, indexed_at)
        VALUES ('s-fts-refresh', '/repo/fts-refresh', 'fts refresh test', 'sync', '2026-05-09T00:00:00Z')
        """
    )
    db.execute(
        """
        INSERT INTO documents (session_id, doc_type, seq, title, stable_id, file_path,
                               file_hash, size_bytes, content_preview, source, indexed_at)
        VALUES ('s-fts-refresh', 'checkpoint', 0, 'FTS Refresh Doc', 'doc-fts-refresh',
                '/repo/fts-refresh.md', 'abc', 42, 'preview', 'sync', '2026-05-09T00:00:00Z')
        """
    )
    doc_row = db.execute("SELECT id FROM documents WHERE stable_id='doc-fts-refresh'").fetchone()
    doc_id = doc_row[0] if doc_row else None
    if doc_id is not None:
        db.execute(
            """
            INSERT INTO sections (document_id, section_name, stable_id, content)
            VALUES (?, 'full', 'sec-fts-refresh', 'native sync section content')
            """,
            (doc_id,),
        )
    db.commit()

    # knowledge_fts should be empty before refresh.
    pre_fts = (
        db.execute("SELECT document_id FROM knowledge_fts WHERE document_id=?", (doc_id,)).fetchone()
        if doc_id
        else None
    )

    # Call the Python FTS refresh function directly (mirrors what pull_once does).
    if doc_id is not None:
        sync_daemon._refresh_knowledge_fts_for_documents(db, {doc_id})
    db.commit()

    post_fts = (
        db.execute("SELECT document_id FROM knowledge_fts WHERE document_id=?", (doc_id,)).fetchone()
        if doc_id
        else None
    )
    db.close()

    test(
        "Python FTS refresh fallback: knowledge_fts row added after _refresh_knowledge_fts_for_documents call",
        pre_fts is None and post_fts is not None,
        f"pre={pre_fts}, post={post_fts}",
    )


def test_fts_refresh_works_for_entries():
    """Python _refresh_ke_fts_for_entries must update ke_fts rows after pull.

    Note: native-sync is now in default Cargo features, so the compiled sk binary handles
    ke_fts refresh natively. This test guards the Python fallback path used by the Python sk.py
    shim and installs without a compiled binary.
    """
    fts_path = ARTIFACT_DIR / "ke-fts-refresh.db"
    make_real_schema_db(fts_path)
    db = sync_daemon.get_db(fts_path)
    sync_daemon.ensure_sync_foundation(db)

    db.execute(
        """
        INSERT INTO sessions (id, path, summary, source, indexed_at)
        VALUES ('s-ke-refresh', '/repo/ke-refresh', 'ke refresh test', 'sync', '2026-05-09T00:00:00Z')
        """
    )
    db.execute(
        """
        INSERT INTO knowledge_entries
            (session_id, category, title, content, stable_id, tags, confidence, topic_key)
        VALUES ('s-ke-refresh', 'pattern', 'ke title', 'ke content', 'ke-fts-refresh',
                'refresh,native', 0.7, 'refresh-topic')
        """
    )
    db.commit()
    entry_row = db.execute("SELECT id FROM knowledge_entries WHERE stable_id='ke-fts-refresh'").fetchone()
    entry_id = entry_row[0] if entry_row else None

    pre_ke_fts = db.execute("SELECT rowid FROM ke_fts WHERE rowid=?", (entry_id,)).fetchone() if entry_id else None

    if entry_id is not None:
        sync_daemon._refresh_ke_fts_for_entries(db, {entry_id})
    db.commit()

    post_ke_fts = db.execute("SELECT rowid FROM ke_fts WHERE rowid=?", (entry_id,)).fetchone() if entry_id else None
    db.close()

    test(
        "Python FTS refresh fallback: ke_fts row added after _refresh_ke_fts_for_entries call",
        pre_ke_fts is None and post_ke_fts is not None,
        f"pre={pre_ke_fts}, post={post_ke_fts}",
    )


def test_usage_md_documents_fts_blocker():
    """docs/USAGE.md must accurately document the current native-sync state.

    Native-sync is now in the default Cargo feature set. USAGE.md should document:
    - native-sync is now in the default build (compiled sk binary routes natively)
    - Python sk.py shim and no-binary installs still delegate to sync-daemon.py
    - no overclaiming that the Python sk.py shim is fully native
    """
    usage_md = REPO / "docs" / "USAGE.md"
    if not usage_md.exists():
        test("docs/USAGE.md exists for sync FTS state check", False, str(usage_md))
        return
    content = usage_md.read_text(encoding="utf-8")
    test(
        "docs/USAGE.md mentions FTS and native-sync (sync hybrid state documented)",
        "FTS" in content or "fts" in content or "native-sync" in content,
        "docs/USAGE.md should mention FTS refresh and native-sync feature boundaries",
    )
    test(
        "docs/USAGE.md does not claim the Python sk.py shim is natively-routed",
        "fully native" not in content.lower() or "native-sync" in content,
        "docs/USAGE.md must not overclaim Rust-only sync run without documenting the shim boundary",
    )


def test_watch_sync_enqueue_module_exists():
    """session.rs must define native sync enqueue and a sessions_fts writer.

    The native Copilot watch indexer enqueues sync_txns/sync_ops rows after indexing
    each document. The function is fail-open — if the sync schema is absent it logs and
    returns without crashing. This is a state 1 (native implementation) surface.
    write_copilot_sessions_fts() closes the prior sessions_fts gap for the
    non-JSONL Copilot path.
    """
    session_rs = REPO / "sk-rust" / "src" / "index" / "session.rs"
    test(
        "sk-rust/src/index/session.rs exists (native sync enqueue)",
        session_rs.exists(),
        "sk-rust/src/index/session.rs not found — native sync enqueue did not land",
    )
    if not session_rs.exists():
        return
    content = session_rs.read_text(encoding="utf-8")
    test(
        "session.rs defines enqueue_doc_sync_op_fail_open (state 1 native)",
        "enqueue_doc_sync_op_fail_open" in content,
        "session.rs missing enqueue_doc_sync_op_fail_open — native sync enqueue not found",
    )
    # Confirm fail-open guard is documented
    test(
        "session.rs documents fail-open behaviour for sync enqueue (no crash if sync schema absent)",
        "fail-open" in content.lower() or "fail_open" in content.lower(),
        "session.rs sync enqueue must be documented as fail-open",
    )
    # Confirm the sessions_fts writer exists.
    test(
        "session.rs defines write_copilot_sessions_fts (native local-only writer)",
        "write_copilot_sessions_fts" in content,
        "session.rs should define write_copilot_sessions_fts()",
    )


def test_usage_md_documents_watch_state():
    """docs/USAGE.md must document the current hybrid watch state accurately.

    sessions_fts is native, but sk watch still is not fully native because
    `extract-knowledge.py` classification and first-run DB creation fallback remain Python.
    """
    usage_md = REPO / "docs" / "USAGE.md"
    if not usage_md.exists():
        test("docs/USAGE.md exists for watch state check", False, str(usage_md))
        return
    content = usage_md.read_text(encoding="utf-8")
    # Must still mention sessions_fts, but now as a native/local-only surface.
    test(
        "docs/USAGE.md documents sessions_fts after watch parity work",
        "sessions_fts" in content and ("native" in content.lower() or "local-only" in content.lower()),
        "docs/USAGE.md must mention sessions_fts as native/local-only",
    )
    # Must still acknowledge that some Python fallback remains.
    test(
        "docs/USAGE.md still documents remaining Python watch fallback (no overclaim)",
        "extract-knowledge.py" in content
        or "first-run db creation" in content.lower()
        or "first-run db" in content.lower(),
        "docs/USAGE.md must still document the remaining Python watch fallback",
    )


test_watch_sync_enqueue_module_exists()
test_usage_md_documents_watch_state()


# ---------------------------------------------------------------------------
# Dream scheduler — timing, disabled state, manual trigger, state persistence
# Issue #162 contract: DreamingScheduler class, dream_interval_hours, etc.
# ---------------------------------------------------------------------------
print("\n── Dream scheduler (issue #162) ────────────────────────────────────────")


def test_dreaming_scheduler_defaults():
    """DreamingScheduler defaults match the issue #162 contract."""
    sched = sync_daemon.DreamingScheduler()
    test("DreamingScheduler.enabled defaults True", sched.enabled is True)
    test(
        "DreamingScheduler.interval_hours defaults 24",
        sched.interval_hours == 24,
        f"got {sched.interval_hours}",
    )
    test(
        "DreamingScheduler.min_score defaults 0.75",
        sched.min_score == 0.75,
        f"got {sched.min_score}",
    )
    test(
        "DreamingScheduler.min_recall_count defaults 3",
        sched.min_recall_count == 3,
        f"got {sched.min_recall_count}",
    )
    test(
        "DreamingScheduler.min_unique_queries defaults 2",
        sched.min_unique_queries == 2,
        f"got {sched.min_unique_queries}",
    )
    test(
        "DreamingScheduler.memory_path defaults MEMORY.md",
        sched.memory_path == "MEMORY.md",
        f"got {sched.memory_path!r}",
    )


def test_dreaming_scheduler_whitespace_memory_path():
    """DreamingScheduler normalizes whitespace-only memory_path to the default."""
    sched = sync_daemon.DreamingScheduler(memory_path="   ")
    test(
        "DreamingScheduler: whitespace-only memory_path normalizes to MEMORY.md",
        sched.memory_path == "MEMORY.md",
        f"got {sched.memory_path!r}",
    )
    # Empty string should also normalize.
    sched2 = sync_daemon.DreamingScheduler(memory_path="")
    test(
        "DreamingScheduler: empty memory_path normalizes to MEMORY.md",
        sched2.memory_path == "MEMORY.md",
        f"got {sched2.memory_path!r}",
    )


def test_dreaming_scheduler_from_config_no_file():
    """DreamingScheduler.from_config falls back to defaults when no config file exists."""
    nonexistent = ARTIFACT_DIR / "sync-config-nonexistent.json"
    sched = sync_daemon.DreamingScheduler.from_config(nonexistent)
    test(
        "from_config: enabled=True when no config",
        sched.enabled is True,
    )
    test(
        "from_config: interval_hours=24 when no config",
        sched.interval_hours == 24,
        f"got {sched.interval_hours}",
    )


def test_dreaming_scheduler_from_config_reads_keys():
    """DreamingScheduler.from_config reads all issue #162 config keys."""
    cfg_path = ARTIFACT_DIR / "sync-config-sched-read.json"
    cfg_path.write_text(
        json.dumps(
            {
                "connection_string": "",
                "dream_enabled": False,
                "dream_interval_hours": 12,
                "dream_min_score": 0.5,
                "dream_min_recall_count": 5,
                "dream_min_unique_queries": 3,
                "dream_memory_path": "/custom/MEMORY.md",
            }
        ),
        encoding="utf-8",
    )
    try:
        sched = sync_daemon.DreamingScheduler.from_config(cfg_path)
        test("from_config: dream_enabled=False", sched.enabled is False)
        test("from_config: dream_interval_hours=12", sched.interval_hours == 12, f"got {sched.interval_hours}")
        test("from_config: dream_min_score=0.5", sched.min_score == 0.5, f"got {sched.min_score}")
        test("from_config: dream_min_recall_count=5", sched.min_recall_count == 5, f"got {sched.min_recall_count}")
        test(
            "from_config: dream_min_unique_queries=3", sched.min_unique_queries == 3, f"got {sched.min_unique_queries}"
        )
        test(
            "from_config: dream_memory_path custom",
            sched.memory_path == "/custom/MEMORY.md",
            f"got {sched.memory_path!r}",
        )
    finally:
        cfg_path.unlink(missing_ok=True)


def test_dream_is_due_no_last_run():
    """DreamingScheduler.is_due returns True when no last_dream_run has been recorded."""
    sched = sync_daemon.DreamingScheduler(interval_hours=24)
    test(
        "is_due: True when no last_dream_run in state",
        sched.is_due({}),
    )


def test_dream_is_due_just_ran():
    """DreamingScheduler.is_due returns False when the sweep ran just now."""
    sched = sync_daemon.DreamingScheduler(interval_hours=24)
    state = {"last_dream_run": sync_daemon.utc_now()}
    test(
        "is_due: False when sweep just completed",
        not sched.is_due(state),
    )


def test_dream_is_due_interval_elapsed():
    """DreamingScheduler.is_due returns True when the configured interval has fully elapsed."""
    import time as _time

    old_ts = (
        datetime.fromtimestamp(_time.time() - 7200, timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    sched = sync_daemon.DreamingScheduler(interval_hours=1)  # 1h = 3600s; 7200s ago
    state = {"last_dream_run": old_ts}
    test(
        "is_due: True when 1h interval has elapsed (last_run 2h ago)",
        sched.is_due(state),
    )


def test_dream_is_due_disabled():
    """DreamingScheduler.is_due returns False when enabled=False, even if overdue."""
    sched = sync_daemon.DreamingScheduler(enabled=False, interval_hours=24)
    test(
        "is_due: False when enabled=False",
        not sched.is_due({}),
    )


def test_dream_is_due_invalid_last_run():
    """DreamingScheduler.is_due treats a corrupt last_dream_run timestamp as overdue."""
    sched = sync_daemon.DreamingScheduler(interval_hours=24)
    state = {"last_dream_run": "not-a-timestamp"}
    test(
        "is_due: True when last_dream_run is malformed",
        sched.is_due(state),
    )


def test_consume_dream_trigger_present():
    """consume_trigger returns True and removes the marker file."""
    marker_dir = ARTIFACT_DIR / "dream-markers"
    marker_dir.mkdir(parents=True, exist_ok=True)
    original_marker = sync_daemon.DREAM_TRIGGER_MARKER
    sync_daemon.DREAM_TRIGGER_MARKER = marker_dir / "dream-trigger.json"
    try:
        sync_daemon.DREAM_TRIGGER_MARKER.write_text(json.dumps({"ts": "2026-01-01T00:00:00Z"}), encoding="utf-8")
        sched = sync_daemon.DreamingScheduler()
        consumed = sched.consume_trigger()
        test(
            "consume_trigger: returns True when marker present",
            consumed is True,
        )
        test(
            "consume_trigger: marker file removed after consumption",
            not sync_daemon.DREAM_TRIGGER_MARKER.exists(),
        )
    finally:
        sync_daemon.DREAM_TRIGGER_MARKER = original_marker


def test_consume_dream_trigger_absent():
    """consume_trigger returns False when no marker file exists."""
    marker_dir = ARTIFACT_DIR / "dream-markers-absent"
    marker_dir.mkdir(parents=True, exist_ok=True)
    original_marker = sync_daemon.DREAM_TRIGGER_MARKER
    sync_daemon.DREAM_TRIGGER_MARKER = marker_dir / "dream-trigger-absent.json"
    try:
        sched = sync_daemon.DreamingScheduler()
        result = sched.consume_trigger()
        test(
            "consume_trigger: returns False when marker absent",
            result is False,
        )
    finally:
        sync_daemon.DREAM_TRIGGER_MARKER = original_marker


def test_last_dream_run_persisted():
    """last_dream_run is written to the state file and survives a reload."""
    state_path = ARTIFACT_DIR / ".dream-state-test.json"
    state_path.unlink(missing_ok=True)

    original_state_file = sync_daemon.STATE_FILE
    sync_daemon.STATE_FILE = state_path
    try:
        state = sync_daemon.load_state()
        test(
            "load_state: last_dream_run defaults to empty string",
            state.get("last_dream_run") == "",
            str(state),
        )
        state["last_dream_run"] = sync_daemon.utc_now()
        sync_daemon.save_state(state)
        reloaded = sync_daemon.load_state()
        test(
            "last_dream_run persists through save_state/load_state round-trip",
            reloaded.get("last_dream_run") == state["last_dream_run"],
            f"saved={state['last_dream_run']!r}  reloaded={reloaded.get('last_dream_run')!r}",
        )
    finally:
        sync_daemon.STATE_FILE = original_state_file
        state_path.unlink(missing_ok=True)


def test_dream_sweep_skipped_when_no_dream_py():
    """run_sweep returns ok=True/skipped=True/promoted_count=0 when dream.py is absent."""
    sched = sync_daemon.DreamingScheduler()
    original_tools_dir = sync_daemon.TOOLS_DIR
    sync_daemon.TOOLS_DIR = ARTIFACT_DIR  # no dream.py here
    try:
        result = sched.run_sweep(db_path=ARTIFACT_DIR / "knowledge.db")
        test(
            "run_sweep: ok=True when dream.py absent (fail-open)",
            result.get("ok") is True,
            str(result),
        )
        test(
            "run_sweep: skipped=True when dream.py absent",
            result.get("skipped") is True,
            str(result),
        )
        test(
            "run_sweep: promoted_count=0 when dream.py absent",
            result.get("promoted_count") == 0,
            str(result),
        )
    finally:
        sync_daemon.TOOLS_DIR = original_tools_dir


def test_dream_sweep_updates_last_run():
    """last_dream_run is NOT updated when sweep is skipped (dream.py absent)."""
    sched = sync_daemon.DreamingScheduler()
    original_tools_dir = sync_daemon.TOOLS_DIR
    sync_daemon.TOOLS_DIR = ARTIFACT_DIR  # triggers skipped path
    try:
        state: dict = {"last_dream_run": ""}
        result = sched.run_sweep(db_path=ARTIFACT_DIR / "knowledge.db")
        if result.get("ok") and not result.get("skipped"):
            state["last_dream_run"] = sync_daemon.utc_now()
        test(
            "run_sweep (skipped): last_dream_run NOT stamped on skipped sweep",
            state["last_dream_run"] == "",
            str(state),
        )
    finally:
        sync_daemon.TOOLS_DIR = original_tools_dir


def test_dream_sweep_no_update_on_failure():
    """last_dream_run is NOT updated when run_sweep returns ok=False."""
    # Patch TOOLS_DIR to a directory that has a fake dream.py that fails.
    import sys as _sys

    fake_dream_dir = ARTIFACT_DIR / "fake-dream-fail"
    fake_dream_dir.mkdir(parents=True, exist_ok=True)
    fake_dream = fake_dream_dir / "dream.py"
    fake_dream.write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
    sched = sync_daemon.DreamingScheduler()
    original_tools_dir = sync_daemon.TOOLS_DIR
    sync_daemon.TOOLS_DIR = fake_dream_dir
    try:
        state: dict = {"last_dream_run": ""}
        result = sched.run_sweep(db_path=ARTIFACT_DIR / "knowledge.db")
        if result.get("ok") and not result.get("skipped"):
            state["last_dream_run"] = sync_daemon.utc_now()
        test(
            "run_sweep (failure): last_dream_run NOT updated on failure",
            state["last_dream_run"] == "",
            str(state),
        )
    finally:
        sync_daemon.TOOLS_DIR = original_tools_dir
        fake_dream.unlink(missing_ok=True)


def test_manual_trigger_wakes_sleep_early():
    """DREAM_TRIGGER_MARKER.exists() check in sleep loop enables early wake."""
    # This tests that the sleep loop will see the marker and break early.
    # We verify the condition logic by checking the marker existence works correctly.
    marker_dir = ARTIFACT_DIR / "dream-sleep-markers"
    marker_dir.mkdir(parents=True, exist_ok=True)
    original_marker = sync_daemon.DREAM_TRIGGER_MARKER
    sync_daemon.DREAM_TRIGGER_MARKER = marker_dir / "dream-trigger.json"
    try:
        # No marker → loop would not break early.
        test(
            "manual trigger: DREAM_TRIGGER_MARKER.exists() False when absent",
            not sync_daemon.DREAM_TRIGGER_MARKER.exists(),
        )
        # Drop marker → loop would break early.
        sync_daemon.DREAM_TRIGGER_MARKER.write_text("{}", encoding="utf-8")
        test(
            "manual trigger: DREAM_TRIGGER_MARKER.exists() True when present",
            sync_daemon.DREAM_TRIGGER_MARKER.exists(),
        )
        # Consuming the marker makes it disappear for the sleep-break check.
        sched = sync_daemon.DreamingScheduler()
        sched.consume_trigger()
        test(
            "manual trigger: consume_trigger removes marker for next sleep iteration",
            not sync_daemon.DREAM_TRIGGER_MARKER.exists(),
        )
    finally:
        sync_daemon.DREAM_TRIGGER_MARKER = original_marker


def test_sync_config_dream_round_trip():
    """sync-config dream_enabled/dream_interval_hours survive a save/load round-trip."""
    cfg_path = ARTIFACT_DIR / "sync-config-dream-rt.json"
    cfg_path.unlink(missing_ok=True)
    original_cfg_path = sync_config.CONFIG_PATH
    original_tools_dir = sync_config.TOOLS_DIR
    sync_config.CONFIG_PATH = cfg_path
    sync_config.TOOLS_DIR = ARTIFACT_DIR
    try:
        sync_config.set_dream_interval_hours(12)
        loaded = sync_config.load_config()
        test(
            "sync-config: dream_interval_hours=12 survives round-trip",
            loaded["dream_interval_hours"] == 12,
            str(loaded),
        )
        sync_config.set_dream_enabled(False)
        loaded = sync_config.load_config()
        test(
            "sync-config: dream_enabled=False survives round-trip",
            loaded["dream_enabled"] is False,
            str(loaded),
        )
        sync_config.set_dream_enabled(True)
        loaded = sync_config.load_config()
        test(
            "sync-config: dream_enabled=True survives round-trip",
            loaded["dream_enabled"] is True,
            str(loaded),
        )
        # Connection string must be preserved when only dream fields are written.
        sync_config.set_connection_string("https://gateway.example.com/")
        sync_config.set_dream_interval_hours(0.5)
        loaded = sync_config.load_config()
        test(
            "sync-config: connection_string preserved when dream_interval_hours written",
            loaded["connection_string"] == "https://gateway.example.com",
            str(loaded),
        )
    finally:
        sync_config.CONFIG_PATH = original_cfg_path
        sync_config.TOOLS_DIR = original_tools_dir
        cfg_path.unlink(missing_ok=True)


def test_sync_config_dream_new_keys_round_trip():
    """sync-config dream_min_score/recall/queries/memory_path survive round-trip."""
    cfg_path = ARTIFACT_DIR / "sync-config-dream-newkeys.json"
    cfg_path.unlink(missing_ok=True)
    original_cfg_path = sync_config.CONFIG_PATH
    original_tools_dir = sync_config.TOOLS_DIR
    sync_config.CONFIG_PATH = cfg_path
    sync_config.TOOLS_DIR = ARTIFACT_DIR
    try:
        sync_config.set_dream_min_score(0.5)
        loaded = sync_config.load_config()
        test("sync-config: dream_min_score=0.5 survives round-trip", loaded["dream_min_score"] == 0.5, str(loaded))

        sync_config.set_dream_min_recall_count(5)
        loaded = sync_config.load_config()
        test(
            "sync-config: dream_min_recall_count=5 survives round-trip",
            loaded["dream_min_recall_count"] == 5,
            str(loaded),
        )

        sync_config.set_dream_min_unique_queries(4)
        loaded = sync_config.load_config()
        test(
            "sync-config: dream_min_unique_queries=4 survives round-trip",
            loaded["dream_min_unique_queries"] == 4,
            str(loaded),
        )

        sync_config.set_dream_memory_path("/custom/path/MEMORY.md")
        loaded = sync_config.load_config()
        test(
            "sync-config: dream_memory_path survives round-trip",
            loaded["dream_memory_path"] == "/custom/path/MEMORY.md",
            str(loaded),
        )
    finally:
        sync_config.CONFIG_PATH = original_cfg_path
        sync_config.TOOLS_DIR = original_tools_dir
        cfg_path.unlink(missing_ok=True)


def test_sync_config_cli_dream_setters():
    """New CLI flags --dream-min-score/recall-count/unique-queries/memory-path round-trip."""
    import importlib
    import sys as _sys

    cfg_path = ARTIFACT_DIR / "sync-config-cli-setters.json"
    cfg_path.unlink(missing_ok=True)
    original_cfg_path = sync_config.CONFIG_PATH
    original_tools_dir = sync_config.TOOLS_DIR
    sync_config.CONFIG_PATH = cfg_path
    sync_config.TOOLS_DIR = ARTIFACT_DIR

    def run_main(*argv):
        saved = _sys.argv[:]
        saved_exit = None
        try:
            _sys.argv = ["sync-config.py"] + list(argv)
            sync_config.main()
        except SystemExit as exc:
            saved_exit = exc.code
        finally:
            _sys.argv = saved
        return saved_exit

    try:
        # --dream-min-score
        exit_code = run_main("--dream-min-score", "0.6")
        test("CLI --dream-min-score: exits 0 (None)", exit_code is None)
        loaded = sync_config.load_config()
        test("CLI --dream-min-score: value persisted", loaded["dream_min_score"] == 0.6, str(loaded))

        # --dream-min-recall-count
        exit_code = run_main("--dream-min-recall-count", "7")
        test("CLI --dream-min-recall-count: exits 0 (None)", exit_code is None)
        loaded = sync_config.load_config()
        test("CLI --dream-min-recall-count: value persisted", loaded["dream_min_recall_count"] == 7, str(loaded))

        # --dream-min-unique-queries
        exit_code = run_main("--dream-min-unique-queries", "4")
        test("CLI --dream-min-unique-queries: exits 0 (None)", exit_code is None)
        loaded = sync_config.load_config()
        test("CLI --dream-min-unique-queries: value persisted", loaded["dream_min_unique_queries"] == 4, str(loaded))

        # --dream-memory-path
        exit_code = run_main("--dream-memory-path", "/custom/MEMORY.md")
        test("CLI --dream-memory-path: exits 0 (None)", exit_code is None)
        loaded = sync_config.load_config()
        test(
            "CLI --dream-memory-path: value persisted", loaded["dream_memory_path"] == "/custom/MEMORY.md", str(loaded)
        )

        # Bad value for --dream-min-score
        exit_code = run_main("--dream-min-score", "not-a-number")
        test("CLI --dream-min-score: exits 1 on bad value", exit_code == 1)

        # Missing value for --dream-min-recall-count
        exit_code = run_main("--dream-min-recall-count")
        test("CLI --dream-min-recall-count: exits 1 when value missing", exit_code == 1)

        # Empty path for --dream-memory-path
        exit_code = run_main("--dream-memory-path", "   ")
        test("CLI --dream-memory-path: exits 1 on empty path", exit_code == 1)
    finally:
        sync_config.CONFIG_PATH = original_cfg_path
        sync_config.TOOLS_DIR = original_tools_dir
        cfg_path.unlink(missing_ok=True)


def test_dreaming_scheduler_from_config_defaults():
    """DreamingScheduler.from_config returns issue #162 defaults when no config file exists."""
    nonexistent = ARTIFACT_DIR / "sync-config-missing.json"
    sched = sync_daemon.DreamingScheduler.from_config(nonexistent)
    test(
        "from_config: enabled=True when no config",
        sched.enabled is True,
    )
    test(
        "from_config: interval_hours=24 when no config",
        sched.interval_hours == 24,
        f"got {sched.interval_hours}",
    )
    test(
        "from_config: min_score=0.75 when no config",
        sched.min_score == 0.75,
        f"got {sched.min_score}",
    )
    test(
        "from_config: min_recall_count=3 when no config",
        sched.min_recall_count == 3,
        f"got {sched.min_recall_count}",
    )
    test(
        "from_config: min_unique_queries=2 when no config",
        sched.min_unique_queries == 2,
        f"got {sched.min_unique_queries}",
    )
    test(
        "from_config: memory_path=MEMORY.md when no config",
        sched.memory_path == "MEMORY.md",
        f"got {sched.memory_path!r}",
    )


test_dreaming_scheduler_defaults()
test_dreaming_scheduler_whitespace_memory_path()
test_dreaming_scheduler_from_config_no_file()
test_dreaming_scheduler_from_config_reads_keys()
test_dream_is_due_no_last_run()
test_dream_is_due_just_ran()
test_dream_is_due_interval_elapsed()
test_dream_is_due_disabled()
test_dream_is_due_invalid_last_run()
test_consume_dream_trigger_present()
test_consume_dream_trigger_absent()
test_last_dream_run_persisted()
test_dream_sweep_skipped_when_no_dream_py()
test_dream_sweep_updates_last_run()
test_dream_sweep_no_update_on_failure()
test_manual_trigger_wakes_sleep_early()
test_sync_config_dream_round_trip()
test_sync_config_dream_new_keys_round_trip()
test_sync_config_cli_dream_setters()
test_dreaming_scheduler_from_config_defaults()


# ---------------------------------------------------------------------------
# Follow-up code-review fixes (issue #162 narrow follow-up)
# Finding 1: consume_trigger must respect dream_enabled=False
# Finding 2: dream_interval_hours=0 must be rejected / normalized
# Finding 3: hot-reload config (covered by from_config round-trip; daemon test here)
# ---------------------------------------------------------------------------
print("\n── Dream scheduler follow-up fixes ──────────────────────────────────────")


def test_consume_trigger_suppressed_when_disabled():
    """consume_trigger returns False and deletes the marker when dream_enabled=False."""
    marker_dir = ARTIFACT_DIR / "dream-disabled-markers"
    marker_dir.mkdir(parents=True, exist_ok=True)
    original_marker = sync_daemon.DREAM_TRIGGER_MARKER
    sync_daemon.DREAM_TRIGGER_MARKER = marker_dir / "dream-trigger-disabled.json"
    try:
        sync_daemon.DREAM_TRIGGER_MARKER.write_text(json.dumps({"ts": "2026-01-01T00:00:00Z"}), encoding="utf-8")
        sched = sync_daemon.DreamingScheduler(enabled=False)
        consumed = sched.consume_trigger()
        test(
            "consume_trigger: returns False when dream_enabled=False",
            consumed is False,
            f"got {consumed!r}",
        )
        test(
            "consume_trigger: marker still removed when dream_enabled=False (no stale accumulation)",
            not sync_daemon.DREAM_TRIGGER_MARKER.exists(),
        )
    finally:
        sync_daemon.DREAM_TRIGGER_MARKER = original_marker


def test_consume_trigger_enabled_still_works():
    """consume_trigger returns True when dream_enabled=True (regression guard)."""
    marker_dir = ARTIFACT_DIR / "dream-enabled-markers"
    marker_dir.mkdir(parents=True, exist_ok=True)
    original_marker = sync_daemon.DREAM_TRIGGER_MARKER
    sync_daemon.DREAM_TRIGGER_MARKER = marker_dir / "dream-trigger-enabled.json"
    try:
        sync_daemon.DREAM_TRIGGER_MARKER.write_text("{}", encoding="utf-8")
        sched = sync_daemon.DreamingScheduler(enabled=True)
        consumed = sched.consume_trigger()
        test(
            "consume_trigger: returns True when dream_enabled=True",
            consumed is True,
        )
    finally:
        sync_daemon.DREAM_TRIGGER_MARKER = original_marker


def test_zero_interval_normalized_in_scheduler():
    """DreamingScheduler normalizes interval_hours=0 to the default (24h)."""
    sched = sync_daemon.DreamingScheduler(interval_hours=0)
    test(
        "DreamingScheduler: interval_hours=0 normalizes to DEFAULT (24)",
        sched.interval_hours == sync_daemon.DEFAULT_DREAM_INTERVAL_HOURS,
        f"got {sched.interval_hours}",
    )


def test_negative_interval_normalized_in_scheduler():
    """DreamingScheduler normalizes negative interval_hours to the default (24h)."""
    sched = sync_daemon.DreamingScheduler(interval_hours=-5)
    test(
        "DreamingScheduler: interval_hours=-5 normalizes to DEFAULT (24)",
        sched.interval_hours == sync_daemon.DEFAULT_DREAM_INTERVAL_HOURS,
        f"got {sched.interval_hours}",
    )


def test_zero_interval_not_stored_by_sync_config():
    """sync-config.set_dream_interval_hours raises ValueError for 0."""
    cfg_path = ARTIFACT_DIR / "sync-config-zero-interval.json"
    cfg_path.unlink(missing_ok=True)
    original_cfg_path = sync_config.CONFIG_PATH
    original_tools_dir = sync_config.TOOLS_DIR
    sync_config.CONFIG_PATH = cfg_path
    sync_config.TOOLS_DIR = ARTIFACT_DIR
    try:
        raised = False
        try:
            sync_config.set_dream_interval_hours(0)
        except ValueError:
            raised = True
        test("sync-config: set_dream_interval_hours(0) raises ValueError", raised)

        raised_neg = False
        try:
            sync_config.set_dream_interval_hours(-1)
        except ValueError:
            raised_neg = True
        test("sync-config: set_dream_interval_hours(-1) raises ValueError", raised_neg)
    finally:
        sync_config.CONFIG_PATH = original_cfg_path
        sync_config.TOOLS_DIR = original_tools_dir
        cfg_path.unlink(missing_ok=True)


def test_zero_interval_cli_exits_1():
    """CLI --dream-interval-hours 0 exits with code 1."""
    import sys as _sys

    cfg_path = ARTIFACT_DIR / "sync-config-cli-zero.json"
    cfg_path.unlink(missing_ok=True)
    original_cfg_path = sync_config.CONFIG_PATH
    original_tools_dir = sync_config.TOOLS_DIR
    sync_config.CONFIG_PATH = cfg_path
    sync_config.TOOLS_DIR = ARTIFACT_DIR

    def run_main(*argv):
        saved = _sys.argv[:]
        exit_code = None
        try:
            _sys.argv = ["sync-config.py"] + list(argv)
            sync_config.main()
        except SystemExit as exc:
            exit_code = exc.code
        finally:
            _sys.argv = saved
        return exit_code

    try:
        exit_code = run_main("--dream-interval-hours", "0")
        test("CLI --dream-interval-hours 0: exits 1", exit_code == 1)

        exit_code = run_main("--dream-interval-hours", "-3")
        test("CLI --dream-interval-hours -3: exits 1", exit_code == 1)
    finally:
        sync_config.CONFIG_PATH = original_cfg_path
        sync_config.TOOLS_DIR = original_tools_dir
        cfg_path.unlink(missing_ok=True)


def test_zero_interval_in_config_file_normalized_on_load():
    """load_config normalizes dream_interval_hours=0 stored in JSON to the default."""
    cfg_path = ARTIFACT_DIR / "sync-config-legacy-zero.json"
    cfg_path.write_text(
        json.dumps({"connection_string": "", "dream_interval_hours": 0}),
        encoding="utf-8",
    )
    original_cfg_path = sync_config.CONFIG_PATH
    original_tools_dir = sync_config.TOOLS_DIR
    sync_config.CONFIG_PATH = cfg_path
    sync_config.TOOLS_DIR = ARTIFACT_DIR
    try:
        loaded = sync_config.load_config()
        test(
            "load_config: dream_interval_hours=0 in JSON normalized to default",
            loaded["dream_interval_hours"] == sync_config.DEFAULT_DREAM_INTERVAL_HOURS,
            f"got {loaded['dream_interval_hours']}",
        )
    finally:
        sync_config.CONFIG_PATH = original_cfg_path
        sync_config.TOOLS_DIR = original_tools_dir
        cfg_path.unlink(missing_ok=True)


def test_from_config_zero_interval_normalized():
    """DreamingScheduler.from_config normalizes dream_interval_hours=0 to default."""
    cfg_path = ARTIFACT_DIR / "sync-config-sched-zero.json"
    cfg_path.write_text(
        json.dumps({"connection_string": "", "dream_interval_hours": 0}),
        encoding="utf-8",
    )
    try:
        sched = sync_daemon.DreamingScheduler.from_config(cfg_path)
        test(
            "from_config: dream_interval_hours=0 in JSON normalized to DEFAULT",
            sched.interval_hours == sync_daemon.DEFAULT_DREAM_INTERVAL_HOURS,
            f"got {sched.interval_hours}",
        )
    finally:
        cfg_path.unlink(missing_ok=True)


def test_hot_reload_config_reflected_in_scheduler():
    """DreamingScheduler.from_config picks up changes written to the config file."""
    cfg_path = ARTIFACT_DIR / "sync-config-hot-reload.json"
    cfg_path.write_text(
        json.dumps({"connection_string": "", "dream_interval_hours": 6, "dream_enabled": True}),
        encoding="utf-8",
    )
    try:
        sched1 = sync_daemon.DreamingScheduler.from_config(cfg_path)
        test(
            "hot-reload: initial interval_hours=6 read from config",
            sched1.interval_hours == 6,
            f"got {sched1.interval_hours}",
        )
        # Simulate operator changing config while daemon is running
        cfg_path.write_text(
            json.dumps({"connection_string": "", "dream_interval_hours": 48, "dream_enabled": False}),
            encoding="utf-8",
        )
        sched2 = sync_daemon.DreamingScheduler.from_config(cfg_path)
        test(
            "hot-reload: updated interval_hours=48 reflected after re-read",
            sched2.interval_hours == 48,
            f"got {sched2.interval_hours}",
        )
        test(
            "hot-reload: dream_enabled=False reflected after re-read",
            sched2.enabled is False,
        )
    finally:
        cfg_path.unlink(missing_ok=True)


test_consume_trigger_suppressed_when_disabled()
test_consume_trigger_enabled_still_works()
test_zero_interval_normalized_in_scheduler()
test_negative_interval_normalized_in_scheduler()
test_zero_interval_not_stored_by_sync_config()
test_zero_interval_cli_exits_1()
test_zero_interval_in_config_file_normalized_on_load()
test_from_config_zero_interval_normalized()
test_hot_reload_config_reflected_in_scheduler()


# ---------------------------------------------------------------------------
# Follow-up: --once mode must not block on scheduled-due dream sweeps (#162)
# ---------------------------------------------------------------------------
print("\n── --once mode dream sweep semantics ────────────────────────────────────")


def test_once_mode_skips_scheduled_dream_sweep():
    """--once mode does NOT run a dream sweep that is only due by schedule (no trigger)."""
    artifact_dir = ARTIFACT_DIR / "once-skip-dream"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    db_path = artifact_dir / "knowledge.db"
    state_path = artifact_dir / ".once-skip-state.json"
    lock_path = artifact_dir / ".once-skip.lock"
    cfg_path = artifact_dir / "sync-config-once-skip.json"
    cfg_path.write_text(json.dumps({"connection_string": ""}), encoding="utf-8")
    marker_dir = artifact_dir / "markers-skip"
    marker_dir.mkdir(parents=True, exist_ok=True)
    # Ensure no trigger marker exists.
    trigger_marker = marker_dir / "dream-trigger.json"
    trigger_marker.unlink(missing_ok=True)

    sweep_called = []
    original_run_sweep = sync_daemon.DreamingScheduler.run_sweep

    def fake_run_sweep(self, *, db_path=None):
        sweep_called.append(True)
        return {"ok": True, "skipped": True, "promoted_count": 0}

    saved = {
        k: getattr(sync_daemon, k)
        for k in [
            "SESSION_STATE",
            "LOCK_FILE",
            "STATE_FILE",
            "DB_PATH",
            "SYNC_CONFIG_PATH",
            "DREAM_TRIGGER_MARKER",
            "MARKERS_DIR",
            "SYNC_NUDGE_MARKER",
            "SYNC_FLUSH_MARKER",
        ]
    }
    try:
        sync_daemon.SESSION_STATE = artifact_dir
        sync_daemon.LOCK_FILE = lock_path
        sync_daemon.STATE_FILE = state_path
        sync_daemon.DB_PATH = db_path
        sync_daemon.SYNC_CONFIG_PATH = cfg_path
        sync_daemon.MARKERS_DIR = marker_dir
        sync_daemon.DREAM_TRIGGER_MARKER = trigger_marker
        sync_daemon.SYNC_NUDGE_MARKER = marker_dir / "sync-nudge.json"
        sync_daemon.SYNC_FLUSH_MARKER = marker_dir / "sync-flush.json"
        sync_daemon.DreamingScheduler.run_sweep = fake_run_sweep

        # State has no last_dream_run → is_due() returns True, but --once must NOT call run_sweep.
        code = sync_daemon.run_loop(once=True)
        test(
            "--once (no trigger): exits 0",
            code == 0,
            f"exit code {code}",
        )
        test(
            "--once (no trigger): scheduled sweep NOT called even when due",
            len(sweep_called) == 0,
            f"run_sweep called {len(sweep_called)} time(s)",
        )
    finally:
        sync_daemon.DreamingScheduler.run_sweep = original_run_sweep
        for k, v in saved.items():
            setattr(sync_daemon, k, v)
        lock_path.unlink(missing_ok=True)


def test_once_mode_runs_dream_on_manual_trigger():
    """--once mode DOES run a dream sweep when an explicit manual trigger marker is present."""
    artifact_dir = ARTIFACT_DIR / "once-trigger-dream"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    db_path = artifact_dir / "knowledge.db"
    state_path = artifact_dir / ".once-trigger-state.json"
    lock_path = artifact_dir / ".once-trigger.lock"
    cfg_path = artifact_dir / "sync-config-once-trigger.json"
    cfg_path.write_text(json.dumps({"connection_string": ""}), encoding="utf-8")
    marker_dir = artifact_dir / "markers-trigger"
    marker_dir.mkdir(parents=True, exist_ok=True)
    trigger_marker = marker_dir / "dream-trigger.json"

    sweep_called = []
    original_run_sweep = sync_daemon.DreamingScheduler.run_sweep

    def fake_run_sweep(self, *, db_path=None):
        sweep_called.append(True)
        return {"ok": True, "skipped": True, "promoted_count": 0}

    saved = {
        k: getattr(sync_daemon, k)
        for k in [
            "SESSION_STATE",
            "LOCK_FILE",
            "STATE_FILE",
            "DB_PATH",
            "SYNC_CONFIG_PATH",
            "DREAM_TRIGGER_MARKER",
            "MARKERS_DIR",
            "SYNC_NUDGE_MARKER",
            "SYNC_FLUSH_MARKER",
        ]
    }
    try:
        sync_daemon.SESSION_STATE = artifact_dir
        sync_daemon.LOCK_FILE = lock_path
        sync_daemon.STATE_FILE = state_path
        sync_daemon.DB_PATH = db_path
        sync_daemon.SYNC_CONFIG_PATH = cfg_path
        sync_daemon.MARKERS_DIR = marker_dir
        sync_daemon.DREAM_TRIGGER_MARKER = trigger_marker
        sync_daemon.SYNC_NUDGE_MARKER = marker_dir / "sync-nudge.json"
        sync_daemon.SYNC_FLUSH_MARKER = marker_dir / "sync-flush.json"
        sync_daemon.DreamingScheduler.run_sweep = fake_run_sweep

        # Drop a manual trigger marker — run_loop should call run_sweep exactly once.
        trigger_marker.write_text("{}", encoding="utf-8")
        code = sync_daemon.run_loop(once=True)
        test(
            "--once (manual trigger): exits 0",
            code == 0,
            f"exit code {code}",
        )
        test(
            "--once (manual trigger): dream sweep called exactly once",
            len(sweep_called) == 1,
            f"run_sweep called {len(sweep_called)} time(s)",
        )
        test(
            "--once (manual trigger): trigger marker consumed",
            not trigger_marker.exists(),
        )
    finally:
        sync_daemon.DreamingScheduler.run_sweep = original_run_sweep
        for k, v in saved.items():
            setattr(sync_daemon, k, v)
        lock_path.unlink(missing_ok=True)


test_once_mode_skips_scheduled_dream_sweep()
test_once_mode_runs_dream_on_manual_trigger()


# ---------------------------------------------------------------------------
# Final review blockers (wave10-issue-162-final-review-fix)
# Blocker 1: skipped sweep (dream.py absent) must NOT stamp last_dream_run
# Blocker 2: set_dream_memory_path must reject empty / whitespace-only values
# ---------------------------------------------------------------------------
print("\n── Final review blockers (issue #162) ───────────────────────────────────")


def test_last_dream_run_not_stamped_when_dream_py_missing_once_mode():
    """--once mode must NOT stamp last_dream_run when run_sweep returns skipped=True.

    Regression guard for the bug where `if ok or skipped:` unconditionally
    stamped last_dream_run even when dream.py was absent, silencing retries
    for the full interval.
    """
    artifact_dir = ARTIFACT_DIR / "once-skip-stamp"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    db_path = artifact_dir / "knowledge.db"
    state_path = artifact_dir / ".once-skip-stamp-state.json"
    lock_path = artifact_dir / ".once-skip-stamp.lock"
    cfg_path = artifact_dir / "sync-config-once-skip-stamp.json"
    cfg_path.write_text(json.dumps({"connection_string": ""}), encoding="utf-8")
    marker_dir = artifact_dir / "markers-skip-stamp"
    marker_dir.mkdir(parents=True, exist_ok=True)
    trigger_marker = marker_dir / "dream-trigger.json"

    original_run_sweep = sync_daemon.DreamingScheduler.run_sweep

    def fake_skipped_sweep(self, *, db_path=None):
        # Simulates dream.py absent: fail-open skip.
        return {"ok": True, "skipped": True, "error": "", "promoted_count": 0}

    saved = {
        k: getattr(sync_daemon, k)
        for k in [
            "SESSION_STATE",
            "LOCK_FILE",
            "STATE_FILE",
            "DB_PATH",
            "SYNC_CONFIG_PATH",
            "DREAM_TRIGGER_MARKER",
            "MARKERS_DIR",
            "SYNC_NUDGE_MARKER",
            "SYNC_FLUSH_MARKER",
        ]
    }
    try:
        sync_daemon.SESSION_STATE = artifact_dir
        sync_daemon.LOCK_FILE = lock_path
        sync_daemon.STATE_FILE = state_path
        sync_daemon.DB_PATH = db_path
        sync_daemon.SYNC_CONFIG_PATH = cfg_path
        sync_daemon.MARKERS_DIR = marker_dir
        sync_daemon.DREAM_TRIGGER_MARKER = trigger_marker
        sync_daemon.SYNC_NUDGE_MARKER = marker_dir / "sync-nudge.json"
        sync_daemon.SYNC_FLUSH_MARKER = marker_dir / "sync-flush.json"
        sync_daemon.DreamingScheduler.run_sweep = fake_skipped_sweep

        # Drop a manual trigger marker so the dream path is exercised.
        trigger_marker.write_text("{}", encoding="utf-8")
        code = sync_daemon.run_loop(once=True)
        test(
            "--once (skipped sweep): exits 0",
            code == 0,
            f"exit code {code}",
        )
        # Reload persisted state to check last_dream_run.
        reloaded = sync_daemon.load_state()
        test(
            "--once (skipped sweep): last_dream_run NOT stamped when dream.py absent",
            not reloaded.get("last_dream_run"),
            f"last_dream_run={reloaded.get('last_dream_run')!r}",
        )
    finally:
        sync_daemon.DreamingScheduler.run_sweep = original_run_sweep
        for k, v in saved.items():
            setattr(sync_daemon, k, v)
        lock_path.unlink(missing_ok=True)


def test_last_dream_run_stamped_on_real_success_once_mode():
    """--once mode MUST stamp last_dream_run when run_sweep returns ok=True/skipped=False.

    Regression guard to ensure the fix does not suppress the stamp for genuine sweeps.
    """
    artifact_dir = ARTIFACT_DIR / "once-real-stamp"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    db_path = artifact_dir / "knowledge.db"
    state_path = artifact_dir / ".once-real-stamp-state.json"
    lock_path = artifact_dir / ".once-real-stamp.lock"
    cfg_path = artifact_dir / "sync-config-once-real-stamp.json"
    cfg_path.write_text(json.dumps({"connection_string": ""}), encoding="utf-8")
    marker_dir = artifact_dir / "markers-real-stamp"
    marker_dir.mkdir(parents=True, exist_ok=True)
    trigger_marker = marker_dir / "dream-trigger.json"

    original_run_sweep = sync_daemon.DreamingScheduler.run_sweep

    def fake_real_sweep(self, *, db_path=None):
        # Simulates a genuine dream sweep that ran successfully.
        return {"ok": True, "skipped": False, "error": "", "promoted_count": 3}

    saved = {
        k: getattr(sync_daemon, k)
        for k in [
            "SESSION_STATE",
            "LOCK_FILE",
            "STATE_FILE",
            "DB_PATH",
            "SYNC_CONFIG_PATH",
            "DREAM_TRIGGER_MARKER",
            "MARKERS_DIR",
            "SYNC_NUDGE_MARKER",
            "SYNC_FLUSH_MARKER",
        ]
    }
    try:
        sync_daemon.SESSION_STATE = artifact_dir
        sync_daemon.LOCK_FILE = lock_path
        sync_daemon.STATE_FILE = state_path
        sync_daemon.DB_PATH = db_path
        sync_daemon.SYNC_CONFIG_PATH = cfg_path
        sync_daemon.MARKERS_DIR = marker_dir
        sync_daemon.DREAM_TRIGGER_MARKER = trigger_marker
        sync_daemon.SYNC_NUDGE_MARKER = marker_dir / "sync-nudge.json"
        sync_daemon.SYNC_FLUSH_MARKER = marker_dir / "sync-flush.json"
        sync_daemon.DreamingScheduler.run_sweep = fake_real_sweep

        # Drop a manual trigger marker so the dream path is exercised.
        trigger_marker.write_text("{}", encoding="utf-8")
        code = sync_daemon.run_loop(once=True)
        test(
            "--once (real sweep): exits 0",
            code == 0,
            f"exit code {code}",
        )
        reloaded = sync_daemon.load_state()
        test(
            "--once (real sweep): last_dream_run IS stamped on genuine ok sweep",
            bool(reloaded.get("last_dream_run")),
            f"last_dream_run={reloaded.get('last_dream_run')!r}",
        )
    finally:
        sync_daemon.DreamingScheduler.run_sweep = original_run_sweep
        for k, v in saved.items():
            setattr(sync_daemon, k, v)
        lock_path.unlink(missing_ok=True)


def test_set_dream_memory_path_rejects_empty():
    """set_dream_memory_path('') must raise ValueError (consistent with CLI guard)."""
    cfg_path = ARTIFACT_DIR / "sync-config-empty-path.json"
    cfg_path.unlink(missing_ok=True)
    original_cfg_path = sync_config.CONFIG_PATH
    original_tools_dir = sync_config.TOOLS_DIR
    sync_config.CONFIG_PATH = cfg_path
    sync_config.TOOLS_DIR = ARTIFACT_DIR
    try:
        raised = False
        try:
            sync_config.set_dream_memory_path("")
        except ValueError:
            raised = True
        test(
            "set_dream_memory_path(''): raises ValueError",
            raised,
            "expected ValueError for empty dream_memory_path",
        )
    finally:
        sync_config.CONFIG_PATH = original_cfg_path
        sync_config.TOOLS_DIR = original_tools_dir
        cfg_path.unlink(missing_ok=True)


def test_set_dream_memory_path_rejects_whitespace():
    """set_dream_memory_path('   ') must raise ValueError (consistent with CLI guard)."""
    cfg_path = ARTIFACT_DIR / "sync-config-whitespace-path.json"
    cfg_path.unlink(missing_ok=True)
    original_cfg_path = sync_config.CONFIG_PATH
    original_tools_dir = sync_config.TOOLS_DIR
    sync_config.CONFIG_PATH = cfg_path
    sync_config.TOOLS_DIR = ARTIFACT_DIR
    try:
        raised = False
        try:
            sync_config.set_dream_memory_path("   ")
        except ValueError:
            raised = True
        test(
            "set_dream_memory_path('   '): raises ValueError",
            raised,
            "expected ValueError for whitespace-only dream_memory_path",
        )
    finally:
        sync_config.CONFIG_PATH = original_cfg_path
        sync_config.TOOLS_DIR = original_tools_dir
        cfg_path.unlink(missing_ok=True)


def test_set_dream_memory_path_valid_path_persisted():
    """set_dream_memory_path with a valid path must persist correctly (regression guard)."""
    cfg_path = ARTIFACT_DIR / "sync-config-valid-path.json"
    cfg_path.unlink(missing_ok=True)
    original_cfg_path = sync_config.CONFIG_PATH
    original_tools_dir = sync_config.TOOLS_DIR
    sync_config.CONFIG_PATH = cfg_path
    sync_config.TOOLS_DIR = ARTIFACT_DIR
    try:
        returned = sync_config.set_dream_memory_path("/my/MEMORY.md")
        test(
            "set_dream_memory_path('/my/MEMORY.md'): returns normalized path",
            returned == "/my/MEMORY.md",
            f"returned={returned!r}",
        )
        loaded = sync_config.load_config()
        test(
            "set_dream_memory_path('/my/MEMORY.md'): persisted in config",
            loaded.get("dream_memory_path") == "/my/MEMORY.md",
            str(loaded),
        )
    finally:
        sync_config.CONFIG_PATH = original_cfg_path
        sync_config.TOOLS_DIR = original_tools_dir
        cfg_path.unlink(missing_ok=True)


test_last_dream_run_not_stamped_when_dream_py_missing_once_mode()
test_last_dream_run_stamped_on_real_success_once_mode()
test_set_dream_memory_path_rejects_empty()
test_set_dream_memory_path_rejects_whitespace()
test_set_dream_memory_path_valid_path_persisted()


print(f"\nFinal total: {PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
