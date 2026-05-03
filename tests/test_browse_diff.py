#!/usr/bin/env python3
"""
test_browse_diff.py — Tests for F6 Checkpoint Diff Viewer

14 tests:
  D1:  GET /diff returns 200 HTML
  D2:  GET /api/diff returns 200 JSON with correct shape
  D3:  JSON stats contain expected added/removed counts
  D4:  HTML contains diff-output element
  D5:  HTML references diff2html
  D6:  Path traversal via session param → 400
  D7:  Session ID with special chars → 400
  D8:  Auth enforced on /diff → 401
  D9:  Auth enforced on /api/diff → 401
  D10: Missing 'from' parameter → 400
  D11: Missing 'to' parameter → 400
  D12: Non-existent session → 404
  D13: unified_diff contains expected --- and +++ markers
  D14: Checkpoint selectors 'first' and 'latest' work
"""
import http.client
import json
import os
import shutil
import sqlite3
import sys
import tempfile
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


def test(name: str, expr: bool) -> None:
    global _PASS, _FAIL
    if expr:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}")


def _make_minimal_db() -> sqlite3.Connection:
    """Minimal in-memory DB — diff routes don't query the DB."""
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
        INSERT INTO schema_version VALUES (8, 'add_sessions_fts', '2026-01-01');
    """)
    db.execute(
        """CREATE VIRTUAL TABLE sessions_fts USING fts5(
            session_id UNINDEXED, title, user_messages,
            assistant_messages, tool_names, tokenize='unicode61'
        )"""
    )
    db.commit()
    return db


def _make_checkpoint_state(session_id: str = "test-diff-session") -> Path:
    """Create a temp directory with synthetic checkpoint files. Returns the root dir."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="test_browse_diff_"))
    cp_dir = tmp_dir / session_id / "checkpoints"
    cp_dir.mkdir(parents=True)

    # Checkpoint 1 — baseline content
    (cp_dir / "checkpoint_001.md").write_text(
        "<overview>Initial project overview</overview>\n"
        "<history>Project started</history>\n"
        "<work_done>Setup complete</work_done>\n",
        encoding="utf-8",
    )

    # Checkpoint 2 — updated content (some sections changed)
    (cp_dir / "checkpoint_002.md").write_text(
        "<overview>Updated project overview with new details</overview>\n"
        "<history>Project started\nAdded feature X</history>\n"
        "<work_done>Setup complete\nImplemented feature X</work_done>\n"
        "<next_steps>Deploy feature X</next_steps>\n",
        encoding="utf-8",
    )

    # Checkpoint 3 — for 'latest' selector test
    (cp_dir / "checkpoint_003.md").write_text(
        "<overview>Final overview</overview>\n"
        "<history>All done</history>\n",
        encoding="utf-8",
    )

    # index.md
    (cp_dir / "index.md").write_text(
        "| 1 | First Checkpoint | checkpoint_001.md |\n"
        "| 2 | Second Checkpoint | checkpoint_002.md |\n"
        "| 3 | Third Checkpoint | checkpoint_003.md |\n",
        encoding="utf-8",
    )

    return tmp_dir


def _start_server(db: sqlite3.Connection, token: str = "tok") -> tuple:
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
        headers = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, headers, body
    finally:
        conn.close()


def run_all_tests() -> int:
    print("=== test_browse_diff.py ===")

    tmp_dir = _make_checkpoint_state("test-diff-session")
    # Point diff routes at our temp session-state dir
    os.environ["COPILOT_SESSION_STATE"] = str(tmp_dir)

    try:
        # ── D1: GET /diff returns 200 HTML ─────────────────────────────────────
        print("\n-- D1: /diff HTML page returns 200")
        db = _make_minimal_db()
        server, host, port = _start_server(db, token="tok")
        try:
            status, hdrs, body = _get(
                host, port,
                "/diff?token=tok&session=test-diff-session&from=1&to=2",
            )
            test("D1: status 200", status == 200)
            ct = hdrs.get("content-type", "")
            test("D1: content-type HTML", "text/html" in ct)
        finally:
            server.shutdown()

        # ── D2: GET /api/diff returns 200 JSON ─────────────────────────────────
        print("\n-- D2: /api/diff JSON shape")
        db = _make_minimal_db()
        server, host, port = _start_server(db, token="tok")
        try:
            status, hdrs, body = _get(
                host, port,
                "/api/diff?token=tok&session=test-diff-session&from=1&to=2",
            )
            test("D2: status 200", status == 200)
            ct = hdrs.get("content-type", "")
            test("D2: content-type JSON", "application/json" in ct)
            try:
                data = json.loads(body)
                test("D2: has 'unified_diff' key", "unified_diff" in data)
                test("D2: has 'files' key", "files" in data)
                test("D2: has 'stats' key", "stats" in data)
                test("D2: has 'session_id' key", "session_id" in data)
                test("D2: stats has 'added'", "added" in data.get("stats", {}))
                test("D2: stats has 'removed'", "removed" in data.get("stats", {}))
            except (json.JSONDecodeError, KeyError) as exc:
                test("D2: valid JSON with required keys", False)
                print(f"    exception: {exc}")
        finally:
            server.shutdown()

        # ── D3: JSON stats are correct ──────────────────────────────────────────
        print("\n-- D3: JSON stats accuracy")
        db = _make_minimal_db()
        server, host, port = _start_server(db, token="tok")
        try:
            _, _, body = _get(
                host, port,
                "/api/diff?token=tok&session=test-diff-session&from=1&to=2",
            )
            data = json.loads(body)
            stats = data.get("stats", {})
            test("D3: added > 0 (checkpoint 2 has more lines)", stats.get("added", 0) > 0)
            # The diff adds lines but checkpoint 1 content is also changed, so removed >= 0
            test("D3: stats values are integers", (
                isinstance(stats.get("added"), int) and
                isinstance(stats.get("removed"), int)
            ))
        finally:
            server.shutdown()

        # ── D4: HTML contains diff-output element ───────────────────────────────
        print("\n-- D4: HTML structure")
        db = _make_minimal_db()
        server, host, port = _start_server(db, token="tok")
        try:
            _, _, body = _get(
                host, port,
                "/diff?token=tok&session=test-diff-session&from=1&to=2",
            )
            body_str = body.decode("utf-8")
            test("D4: contains diff-output div", 'id="diff-output"' in body_str)
            test("D4: contains diff-controls", 'id="diff-controls"' in body_str)
            test("D4: has side-by-side radio", 'value="side-by-side"' in body_str)
            test("D4: has line-by-line radio", 'value="line-by-line"' in body_str)
        finally:
            server.shutdown()

        # ── D5: HTML references diff2html ───────────────────────────────────────
        print("\n-- D5: vendor script references")
        db = _make_minimal_db()
        server, host, port = _start_server(db, token="tok")
        try:
            _, _, body = _get(
                host, port,
                "/diff?token=tok&session=test-diff-session&from=1&to=2",
            )
            body_str = body.decode("utf-8")
            test("D5: references diff2html.min.js", "diff2html.min.js" in body_str)
            test("D5: references diff2html.min.css", "diff2html.min.css" in body_str)
            test("D5: references diff.js", "diff.js" in body_str)
        finally:
            server.shutdown()

        # ── D6: Path traversal via session param → 400 ─────────────────────────
        print("\n-- D6: path traversal blocked")
        db = _make_minimal_db()
        server, host, port = _start_server(db, token="tok")
        try:
            # Dots are not in the session_id regex — should be 400
            traversal_cases = [
                "/api/diff?token=tok&session=../etc&from=1&to=2",
                "/api/diff?token=tok&session=..%2Fetc&from=1&to=2",
                "/diff?token=tok&session=../../passwd&from=1&to=2",
            ]
            for path in traversal_cases:
                s, _, _ = _get(host, port, path)
                test(f"D6: traversal '{path[:50]}...' → 400", s == 400)
        finally:
            server.shutdown()

        # ── D7: Session ID with special chars → 400 ────────────────────────────
        print("\n-- D7: invalid session_id rejected")
        db = _make_minimal_db()
        server, host, port = _start_server(db, token="tok")
        try:
            bad_cases = [
                "/api/diff?token=tok&session=<script>&from=1&to=2",
                "/api/diff?token=tok&session=" + "a" * 65 + "&from=1&to=2",
                "/api/diff?token=tok&session=abc/def&from=1&to=2",
                "/api/diff?token=tok&session=abc.def&from=1&to=2",
            ]
            for path in bad_cases:
                s, _, _ = _get(host, port, path)
                test(f"D7: invalid session '{path[40:60]}' → 400", s == 400)
        finally:
            server.shutdown()

        # ── D8: Auth enforced on /diff ──────────────────────────────────────────
        print("\n-- D8: auth enforced on /diff")
        db = _make_minimal_db()
        server, host, port = _start_server(db, token="secret")
        try:
            s1, _, _ = _get(host, port, "/diff?session=test-diff-session&from=1&to=2")
            test("D8: /diff without token → 401", s1 == 401)
            s2, _, _ = _get(host, port, "/diff?token=wrong&session=test-diff-session&from=1&to=2")
            test("D8: /diff wrong token → 401", s2 == 401)
            s3, _, _ = _get(host, port, "/diff?token=secret&session=test-diff-session&from=1&to=2")
            test("D8: /diff correct token → 200", s3 == 200)
        finally:
            server.shutdown()

        # ── D9: Auth enforced on /api/diff ─────────────────────────────────────
        print("\n-- D9: auth enforced on /api/diff")
        db = _make_minimal_db()
        server, host, port = _start_server(db, token="secret")
        try:
            s1, _, _ = _get(host, port, "/api/diff?session=test-diff-session&from=1&to=2")
            test("D9: /api/diff without token → 401", s1 == 401)
            s2, _, _ = _get(host, port, "/api/diff?token=wrong&session=test-diff-session&from=1&to=2")
            test("D9: /api/diff wrong token → 401", s2 == 401)
            s3, _, _ = _get(host, port, "/api/diff?token=secret&session=test-diff-session&from=1&to=2")
            test("D9: /api/diff correct token → 200", s3 == 200)
        finally:
            server.shutdown()

        # ── D10: Missing 'from' parameter → 400 ────────────────────────────────
        print("\n-- D10: missing 'from' parameter")
        db = _make_minimal_db()
        server, host, port = _start_server(db, token="tok")
        try:
            s, _, _ = _get(host, port, "/api/diff?token=tok&session=test-diff-session&to=2")
            test("D10: missing 'from' → 400", s == 400)
        finally:
            server.shutdown()

        # ── D11: Missing 'to' parameter → 400 ──────────────────────────────────
        print("\n-- D11: missing 'to' parameter")
        db = _make_minimal_db()
        server, host, port = _start_server(db, token="tok")
        try:
            s, _, _ = _get(host, port, "/api/diff?token=tok&session=test-diff-session&from=1")
            test("D11: missing 'to' → 400", s == 400)
        finally:
            server.shutdown()

        # ── D12: Non-existent session → 404 ────────────────────────────────────
        print("\n-- D12: non-existent session → 404")
        db = _make_minimal_db()
        server, host, port = _start_server(db, token="tok")
        try:
            s, _, _ = _get(host, port, "/api/diff?token=tok&session=no-such-session&from=1&to=2")
            test("D12: non-existent session → 404", s == 404)
        finally:
            server.shutdown()

        # ── D13: unified_diff contains expected markers ─────────────────────────
        print("\n-- D13: unified_diff markers")
        db = _make_minimal_db()
        server, host, port = _start_server(db, token="tok")
        try:
            _, _, body = _get(
                host, port,
                "/api/diff?token=tok&session=test-diff-session&from=1&to=2",
            )
            data = json.loads(body)
            ud = data.get("unified_diff", "")
            test("D13: unified_diff has '---' marker", "---" in ud)
            test("D13: unified_diff has '+++' marker", "+++" in ud)
            test("D13: unified_diff has '+' added lines", any(
                l.startswith("+") and not l.startswith("+++") for l in ud.splitlines()
            ))
        finally:
            server.shutdown()

        # ── D14: 'first' and 'latest' selectors work ───────────────────────────
        print("\n-- D14: checkpoint selectors 'first' and 'latest'")
        db = _make_minimal_db()
        server, host, port = _start_server(db, token="tok")
        try:
            s1, _, _ = _get(
                host, port,
                "/api/diff?token=tok&session=test-diff-session&from=first&to=latest",
            )
            test("D14: from=first&to=latest → 200", s1 == 200)
            # first == latest when same checkpoint → but here first=1, latest=3 so different
            _, _, body = _get(
                host, port,
                "/api/diff?token=tok&session=test-diff-session&from=first&to=latest",
            )
            data = json.loads(body)
            test("D14: from.seq == 1", data.get("from", {}).get("seq") == 1)
            test("D14: to.seq == 3", data.get("to", {}).get("seq") == 3)
        finally:
            server.shutdown()

    finally:
        # Cleanup temp session state
        try:
            shutil.rmtree(str(tmp_dir), ignore_errors=True)
        except Exception:
            pass
        os.environ.pop("COPILOT_SESSION_STATE", None)

    print(f"\n{'=' * 50}")
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    return _FAIL


if __name__ == "__main__":
    sys.exit(run_all_tests())
