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
                            "indexed_at": "2026-01-01T00:00:00Z"
                        },
                        "op_index": 0,
                        "created_at": "2026-01-01T00:00:00Z"
                    }
                ]
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
            self._write(200, {
                "accepted_txn_ids": txn_ids,
                "duplicate_txn_ids": [],
                "latest_txn_id": txn_ids[-1] if txn_ids else ""
            })

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
db.execute(
    "INSERT OR REPLACE INTO sync_state (key, value) VALUES ('local_replica_id', 'local-test')"
)
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
        json.dumps({
            "id": "session-local-1",
            "path": "/repo/local",
            "summary": "local row",
            "total_checkpoints": 1,
            "total_research": 0,
            "total_files": 0,
            "has_plan": 1,
            "source": "copilot",
            "indexed_at": "2026-01-01T00:00:00Z",
        }),
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
test("sync-daemon seeds machine-specific replica id", seeded_id.startswith("local-") and seeded_id != "local", seeded_id)
stored_seeded = db.execute("SELECT value FROM sync_state WHERE key='local_replica_id'").fetchone()
test("sync-daemon persists seeded replica id", stored_seeded is not None and stored_seeded[0] == seeded_id, str(stored_seeded))
db.execute("UPDATE sync_state SET value='local' WHERE key='local_replica_id'")
migrated_id = sync_daemon.get_local_replica_id(db)
test("sync-daemon migrates legacy local replica id", migrated_id.startswith("local-") and migrated_id != "local", migrated_id)
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
remote_status = db.execute(
    "SELECT status FROM sync_txns WHERE txn_id='remote-pending-filter'"
).fetchone()
pending_ids = [
    t["txn_id"]
    for t in sync_daemon.collect_pending_txns(db, limit=20, replica_id="local-test")
]
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
                json.dumps({
                    "id": f"session-{txn_id}",
                    "path": f"/repo/{txn_id}",
                    "summary": txn_id,
                    "indexed_at": now,
                }),
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
        test("unsent txn_ids remain pending after rejected gateway response", all(r[1] == "pending" for r in rows), str(rows))
    finally:
        sync_daemon._request_json = original_request_json
        db.execute(
            "DELETE FROM sync_ops WHERE txn_id IN ('local-txn-response-check', 'local-txn-unsent')"
        )
        db.execute(
            "DELETE FROM sync_txns WHERE txn_id IN ('local-txn-response-check', 'local-txn-unsent')"
        )
        db.commit()
        db.close()

    status_obj = sync_status.collect_status(db_path=db_path, check_health=True)
    test("sync-status finds local replica id", status_obj["local_replica_id"] == "local-test")
    test("sync-status gateway reachable", status_obj["gateway_health"]["status"] == "ok", str(status_obj["gateway_health"]))
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
    cursor2 = db2.execute(
        "SELECT last_txn_id FROM sync_cursors WHERE replica_id='local-runtime-2'"
    ).fetchone()
    test("pull fail-open ignores unknown future table", pull_result["applied"] == 2, str(pull_result))
    test("pull still applies known canonical op", remote2 is not None and remote2[0] == "remote row 2", str(remote2))
    test("pull cursor advances after unknown table op", cursor2 is not None and cursor2[0] == "remote-unknown-2", str(cursor2))

    db2.execute(
        "UPDATE sync_table_policies SET stable_id_column='bad-col' WHERE table_name='sessions'"
    )
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
    fallback_row = db2.execute(
        "SELECT summary FROM sessions WHERE id='session-invalid-stable-col'"
    ).fetchone()
    test("invalid stable_id_column is validated and does not break apply", fallback_row is not None and fallback_row[0] == "stable col fallback", str(fallback_row))
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

    session_contract = db4.execute(
        "SELECT id, summary FROM sessions WHERE id='session-contract-1'"
    ).fetchone()
    session_after_fail = db4.execute(
        "SELECT id, summary FROM sessions WHERE id='session-after-fail-open'"
    ).fetchone()
    ignored_session = db4.execute(
        "SELECT id FROM sessions WHERE id='remote-session-id-ignored'"
    ).fetchone()
    doc_row = db4.execute(
        "SELECT id, stable_id FROM documents WHERE stable_id='doc-contract-1'"
    ).fetchone()
    doc_payload_mismatch = db4.execute(
        "SELECT id FROM documents WHERE stable_id='doc-payload-mismatch'"
    ).fetchone()
    section_row = db4.execute(
        "SELECT document_id FROM sections WHERE stable_id='section-contract-1'"
    ).fetchone()
    ke_one = db4.execute(
        "SELECT id, document_id FROM knowledge_entries WHERE stable_id='ke-contract-1'"
    ).fetchone()
    ke_two = db4.execute(
        "SELECT id FROM knowledge_entries WHERE stable_id='ke-contract-2'"
    ).fetchone()
    kr_row = db4.execute(
        "SELECT source_id, target_id FROM knowledge_relations WHERE stable_id='kr-contract-1'"
    ).fetchone()
    er_row = db4.execute(
        "SELECT subject, predicate, object FROM entity_relations WHERE stable_id='er-contract-1'"
    ).fetchone()
    sf_row = db4.execute(
        "SELECT id, stable_id, origin_replica_id FROM search_feedback WHERE stable_id='sf-contract-1'"
    ).fetchone()
    sf_empty_row = db4.execute(
        "SELECT id FROM search_feedback WHERE stable_id=''"
    ).fetchone()
    ke_fts_row = db4.execute(
        "SELECT rowid, title FROM ke_fts WHERE rowid = ?",
        (ke_one[0],),
    ).fetchone() if ke_one is not None else None
    knowledge_fts_row = db4.execute(
        "SELECT document_id, content FROM knowledge_fts WHERE document_id = ?",
        (doc_row[0],),
    ).fetchone() if doc_row is not None else None
    cursor4 = db4.execute(
        "SELECT last_txn_id FROM sync_cursors WHERE replica_id='local-runtime-contract'"
    ).fetchone()
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
    test("sessions use row_stable_id instead of remote id", session_contract is not None and ignored_session is None, str(session_contract))
    test("documents ignore remote surrogate id", doc_row is not None and doc_row[0] != 9001, str(doc_row))
    test("documents keep envelope row_stable_id authoritative over payload stable_id", doc_row is not None and doc_row[1] == "doc-contract-1" and doc_payload_mismatch is None, str(doc_row))
    test("sections resolve document_stable_id to local document_id", section_row is not None and doc_row is not None and section_row[0] == doc_row[0], str(section_row))
    test("knowledge_entries resolve optional document_stable_id", ke_one is not None and doc_row is not None and ke_one[1] == doc_row[0], str(ke_one))
    test("knowledge_relations resolve source/target local ids", kr_row is not None and ke_one is not None and ke_two is not None and kr_row[0] == ke_one[0] and kr_row[1] == ke_two[0], str(kr_row))
    test("entity_relations apply by stable_id contract", er_row is not None and er_row[0] == "sync", str(er_row))
    test("search_feedback applies without trusting remote id", sf_row is not None and sf_row[0] != 4001 and sf_row[1] == "sf-contract-1", str(sf_row))
    test("empty row_stable_id delete is ignored", sf_empty_row is not None, str(sf_empty_row))
    test("pull refreshes ke_fts for synced knowledge entries", ke_fts_row is not None and ke_fts_row[0] == ke_one[0], str(ke_fts_row))
    test("pull refreshes knowledge_fts for synced sections", knowledge_fts_row is not None and "section content" in knowledge_fts_row[1], str(knowledge_fts_row))
    test("per-op failure is fail-open for remaining ops", session_after_fail is not None and session_after_fail[1] == "still applied", str(session_after_fail))
    test("pull cursor advances after partial-op failures", cursor4 is not None and cursor4[0] == "remote-contract-2", str(cursor4))
    test("failed unresolved helper reference is recorded", failure4 is not None and failure4[0] == "sections", str(failure4))

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

    seeded_doc = db_delete.execute(
        "SELECT id FROM documents WHERE stable_id='doc-delete-1'"
    ).fetchone()
    seeded_entry = db_delete.execute(
        "SELECT id FROM knowledge_entries WHERE stable_id='ke-delete-1'"
    ).fetchone()
    seeded_knowledge_fts = db_delete.execute(
        "SELECT document_id FROM knowledge_fts WHERE document_id=?",
        (seeded_doc[0],),
    ).fetchone() if seeded_doc is not None else None
    seeded_ke_fts = db_delete.execute(
        "SELECT rowid FROM ke_fts WHERE rowid=?",
        (seeded_entry[0],),
    ).fetchone() if seeded_entry is not None else None
    test("delete regression seed pull applied", seeded_pull["applied"] == 1, str(seeded_pull))
    test("delete regression seed creates knowledge_fts row", seeded_knowledge_fts is not None, str(seeded_knowledge_fts))
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

    deleted_doc = db_delete.execute(
        "SELECT id FROM documents WHERE stable_id='doc-delete-1'"
    ).fetchone()
    deleted_entry = db_delete.execute(
        "SELECT id FROM knowledge_entries WHERE stable_id='ke-delete-1'"
    ).fetchone()
    stale_knowledge_fts = db_delete.execute(
        "SELECT document_id FROM knowledge_fts WHERE document_id=?",
        (seeded_doc[0],),
    ).fetchone() if seeded_doc is not None else None
    stale_ke_fts = db_delete.execute(
        "SELECT rowid FROM ke_fts WHERE rowid=?",
        (seeded_entry[0],),
    ).fetchone() if seeded_entry is not None else None
    db_delete.close()

    test("delete regression pull applied", delete_pull["applied"] == 1, str(delete_pull))
    test("remote document delete removes canonical document row", deleted_doc is None, str(deleted_doc))
    test("remote knowledge entry delete removes canonical entry row", deleted_entry is None, str(deleted_entry))
    test("remote document delete removes stale knowledge_fts row", stale_knowledge_fts is None, str(stale_knowledge_fts))
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
    ("local-txn-flush", "local-test-flush", datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")),
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
    test("flush marker still performs pull", remote_flush_row is not None and remote_flush_row[0] == "remote flush row", str(remote_flush_row))
    test("flush marker push commits pending txn", local_txn_state is not None and local_txn_state[0] == "committed", str(local_txn_state))
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
    page_rows = db5.execute("SELECT COUNT(*) FROM sessions WHERE id IN ('session-page-1','session-page-2')").fetchone()[0]
    page_cursor = db5.execute("SELECT last_txn_id FROM sync_cursors WHERE replica_id='local-pagination'").fetchone()
    db5.close()
    test("pull pagination fetches multiple pages in one cycle", len(request_calls) >= 2, str(request_calls))
    test("pull pagination applies all paged txns", paged_pull["applied"] == 2 and page_rows == 2, str(paged_pull))
    test("pull pagination advances cursor to last page", page_cursor is not None and page_cursor[0] == "remote-page-2", str(page_cursor))
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

print("\n🔧 sync-knowledge runtime status summary")
db = sqlite3.connect(str(db_path))
status = sync_knowledge._sync_runtime_status(db)

test("sync-knowledge runtime reflects configured gateway", status["configured"] is True)
test("sync-knowledge runtime pending count is zero after push", status["pending_txns"] == 0, str(status))
db.close()

print(f"\nResult: {PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
