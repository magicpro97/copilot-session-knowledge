#!/usr/bin/env python3
"""
tests/test_visual_snapshot.py — SHA256 visual snapshot tests for stable routes.

First run: writes baseline snapshots to tests/snapshots/<slug>.sha256.
Subsequent runs: compares against baseline; fails on drift.
UPDATE_SNAPSHOTS=1 python3 tests/test_visual_snapshot.py — regenerates baselines.
"""

import hashlib
import http.client
import os
import re
import sqlite3
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))
import browse

_PASS = 0
_FAIL = 0

SNAPSHOTS_DIR = Path(__file__).parent / "snapshots"
UPDATE_MODE = os.environ.get("UPDATE_SNAPSHOTS", "").strip() not in ("", "0")

ROUTES = [
    ("/", "root"),
    ("/sessions", "sessions"),
    ("/dashboard", "dashboard"),
    ("/style-guide", "style-guide"),
]


def test(name: str, expr: bool) -> None:
    global _PASS, _FAIL
    if expr:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}")


def _make_test_db() -> sqlite3.Connection:
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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL DEFAULT '',
            document_id INTEGER,
            category TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
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
            est_tokens INTEGER DEFAULT 0
        );
        CREATE TABLE entity_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL DEFAULT '',
            predicate TEXT NOT NULL DEFAULT '',
            object TEXT NOT NULL DEFAULT '',
            noted_at TEXT DEFAULT (datetime('now')),
            session_id TEXT DEFAULT ''
        );
        CREATE TABLE embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id INTEGER,
            model TEXT,
            vector BLOB
        );
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT
        );
        INSERT INTO schema_version VALUES (8, 'add_sessions_fts', '2026-01-01');
    """)
    db.execute(
        """CREATE VIRTUAL TABLE ke_fts USING fts5(
            title, content, tokenize='unicode61'
        )"""
    )
    db.execute(
        """CREATE VIRTUAL TABLE sessions_fts USING fts5(
            session_id UNINDEXED, title, user_messages,
            assistant_messages, tool_names, tokenize='unicode61'
        )"""
    )
    db.execute(
        "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("abc-123-def-456", "/path/to/session", "Sample test session", "copilot",
         1.0, 2.0, 3.0, 10, 1024, 1, 0, 3, 0, "2026-01-01"),
    )
    db.execute(
        "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (1, "abc-123-def-456", "checkpoint", 1, "Checkpoint 1",
         "/path", "abc", 100, "preview", "2026-01-01", "copilot"),
    )
    db.execute(
        "INSERT INTO sections VALUES (?,?,?,?)",
        (1, 1, "overview", "Session overview content for test"),
    )
    db.execute(
        "INSERT INTO sessions_fts VALUES (?,?,?,?,?)",
        ("abc-123-def-456", "Sample test session", "user asked about X",
         "assistant replied with Y", "tool_call"),
    )
    db.commit()
    return db


def _start_server(db: sqlite3.Connection, token: str = "testtoken") -> tuple:
    HandlerClass = browse._make_handler_class(db, token)
    server = ThreadingHTTPServer(("127.0.0.1", 0), HandlerClass)
    host, port = server.server_address
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.05)
    return server, host, port


def _get(host: str, port: int, path: str) -> tuple:
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        return resp.status, body
    finally:
        conn.close()


def _normalize_html(html: str) -> str:
    """Strip volatile bits before hashing."""
    # Strip nonce="..." attributes
    html = re.sub(r'nonce="[^"]*"', 'nonce="X"', html)
    # Strip CSRF/auth tokens in query strings
    html = re.sub(r'token=[a-zA-Z0-9_\-]+', 'token=X', html)
    # Strip dynamic timestamps (ISO-like strings in content)
    html = re.sub(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', 'TIMESTAMP', html)
    return html


def _hash(html: str) -> str:
    return hashlib.sha256(html.encode("utf-8")).hexdigest()


def _snapshot_path(slug: str) -> Path:
    return SNAPSHOTS_DIR / f"{slug}.sha256"


def run_all_tests() -> int:
    print("=== tests/test_visual_snapshot.py ===")
    if UPDATE_MODE:
        print("  (UPDATE_SNAPSHOTS=1 — regenerating baselines)")

    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    db = _make_test_db()
    server, host, port = _start_server(db, token="testtoken")

    try:
        for route_path, slug in ROUTES:
            full_path = f"{route_path}?token=testtoken"
            status, body = _get(host, port, full_path)

            test(f"snapshot/{slug}: HTTP 200", status == 200)
            if status != 200:
                continue

            html = body.decode("utf-8", errors="replace")
            normalized = _normalize_html(html)
            current_hash = _hash(normalized)

            snap_file = _snapshot_path(slug)
            if UPDATE_MODE or not snap_file.exists():
                snap_file.write_text(current_hash, encoding="utf-8")
                print(f"  WROTE baseline for {slug}: {current_hash[:16]}…")
                test(f"snapshot/{slug}: baseline written", True)
            else:
                stored_hash = snap_file.read_text(encoding="utf-8").strip()
                match = current_hash == stored_hash
                if not match:
                    print(
                        f"  FAIL  snapshot/{slug}: hash mismatch\n"
                        f"    stored : {stored_hash[:16]}…\n"
                        f"    current: {current_hash[:16]}…\n"
                        f"    Run UPDATE_SNAPSHOTS=1 python3 tests/test_visual_snapshot.py to refresh."
                    )
                test(f"snapshot/{slug}: hash matches baseline", match)

    finally:
        server.shutdown()

    print(f"\n{'='*40}")
    print(f"PASSED: {_PASS}  FAILED: {_FAIL}")
    return _FAIL


if __name__ == "__main__":
    sys.exit(run_all_tests())
