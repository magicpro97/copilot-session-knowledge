#!/usr/bin/env python3
"""tests/test_ui_components.py — Unit + integration tests for browse/components/primitives.py."""
import http.client
import os
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

from browse.components.primitives import (
    badge, banner, card, data_table, empty_state, page_header, stat_grid,
)
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


# ── DB fixture (copied from tests/test_ui_foundation.py) ─────────────────────

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
    db.execute("""CREATE VIRTUAL TABLE ke_fts USING fts5(
        title, content, tokenize='unicode61'
    )""")
    db.execute("""CREATE VIRTUAL TABLE sessions_fts USING fts5(
        session_id UNINDEXED, title, user_messages,
        assistant_messages, tool_names, tokenize='unicode61'
    )""")
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


# ── Unit tests ────────────────────────────────────────────────────────────────

def test_stat_grid():
    print("\n-- test_stat_grid")
    result = stat_grid([("42", "Sessions"), ("7", "Errors")])
    test("stat_grid: has stat-grid class", 'class="stat-grid"' in result)
    test("stat_grid: has stat-card class", 'class="stat-card"' in result)
    test("stat_grid: has stat-value class", 'class="stat-value"' in result)
    test("stat_grid: has stat-label class", 'class="stat-label"' in result)
    test("stat_grid: label text present", "Sessions" in result)
    test("stat_grid: empty list → empty string", stat_grid([]) == "")


def test_data_table():
    print("\n-- test_data_table")
    result = data_table(["Name", "Count"], [["Alice", "3"], ["Bob", "5"]])
    test("data_table: has thead", "<thead>" in result)
    test("data_table: has tbody", "<tbody>" in result)
    test("data_table: cell count", result.count("<td>") == 4)
    empty = data_table(["Name"], [])
    test("data_table: empty rows → empty-state", 'class="empty-state"' in empty)
    test("data_table: empty_title present", "No data yet" in empty)


def test_data_table_escapes_headers():
    print("\n-- test_data_table_escapes_headers")
    result = data_table(["<script>"], [["cell"]])
    test("data_table: header escaped", "&lt;script&gt;" in result)
    test("data_table: raw tag absent", "<script>" not in result)


def test_data_table_preserves_cell_html():
    print("\n-- test_data_table_preserves_cell_html")
    cell = '<a href="x">y</a>'
    result = data_table(["Link"], [[cell]])
    test("data_table: cell html preserved", cell in result)


def test_banner_variants():
    print("\n-- test_banner_variants")
    test("banner info", 'class="banner banner-info"' in banner("hi", "info"))
    test("banner warning → default class", 'class="banner"' in banner("hi", "warning"))
    test("banner error", 'class="banner banner-error"' in banner("hi", "error"))
    test("banner unknown → info", 'class="banner banner-info"' in banner("hi", "unknown"))


def test_empty_state_shape():
    print("\n-- test_empty_state_shape")
    result = empty_state("📭", "Nothing here", "Try later", '<a href="#">Go</a>')
    test("empty_state: icon class", 'class="empty-state-icon"' in result)
    test("empty_state: title class", 'class="empty-state-title"' in result)
    test("empty_state: title escaped", "Nothing here" in result)
    test("empty_state: action_html raw", '<a href="#">Go</a>' in result)


def test_badge_variant_clamp():
    print("\n-- test_badge_variant_clamp")
    result = badge("x", "unknown")
    test("badge unknown variant → badge-info", 'class="badge badge-info"' in result)


def test_page_header_shape():
    print("\n-- test_page_header_shape")
    result = page_header("My Title", subtitle_html="subtitle here")
    test("page_header: header element", 'class="page-header"' in result)
    test("page_header: h2 element", "<h2>" in result)
    test("page_header: subtitle present", "subtitle here" in result)
    no_sub = page_header("Title Only")
    test("page_header: no subtitle when empty", "<p" not in no_sub)


def test_page_header_level_param():
    print("\n-- test_page_header_level_param")
    test("page_header level=3 emits h3", "<h3>" in page_header("X", level=3))
    test("page_header level=2 emits h2", "<h2>" in page_header("X", level=2))
    test("page_header level=1 clamps to h2", "<h2>" in page_header("X", level=1))
    test("page_header level=99 clamps to h2", "<h2>" in page_header("X", level=99))


def test_page_header_css_classes_exist():
    print("\n-- test_page_header_css_classes_exist")
    css_path = Path(__file__).parent.parent / "browse" / "static" / "css" / "app.css"
    css = css_path.read_text(encoding="utf-8")
    test("css: .page-header { present", ".page-header {" in css)
    test("css: .page-header-actions { present", ".page-header-actions {" in css)


def test_card_shape():
    result = card("body content", header_html="hdr", footer_html="ftr")
    test("card: section.card", 'class="card"' in result)
    test("card: body present", "body content" in result)
    test("card: header slot", "<header>hdr</header>" in result)
    test("card: footer slot", "<footer>ftr</footer>" in result)
    no_extras = card("just body")
    test("card: no extra header/footer", "<header>" not in no_extras and "<footer>" not in no_extras)


# ── Integration tests ─────────────────────────────────────────────────────────

def run_integration_tests(host: str, port: int) -> None:
    print("\n-- test_dashboard_uses_components")
    status, body = _get(host, port, "/dashboard?token=testtoken")
    html = body.decode("utf-8", errors="replace")
    test("dashboard: status 200", status == 200)
    test("dashboard: has stat-grid", 'class="stat-grid"' in html)
    test("dashboard: has stat-card", 'class="stat-card"' in html)
    test("dashboard: has table-wrapper", 'class="table-wrapper"' in html or 'class="empty-state"' in html)
    test("dashboard: no db-kpi-tile", ".db-kpi-tile" not in html)
    test("dashboard: no db-kpi- class attr", 'class="db-kpi-' not in html)

    print("\n-- test_home_no_inline_style")
    status, body = _get(host, port, "/?token=testtoken")
    html = body.decode("utf-8", errors="replace")
    test("home: status 200", status == 200)
    test("home: no padding:0.5rem style", "style=\"padding:0.5rem" not in html)
    test("home: no card-background inline style", "style=" not in html.split("<main")[1].split("</main>")[0] if "<main" in html else True)

    print("\n-- test_sessions_uses_data_table")
    status, body = _get(host, port, "/sessions?token=testtoken")
    html = body.decode("utf-8", errors="replace")
    test("sessions: status 200", status == 200)
    test("sessions: has table-wrapper", 'class="table-wrapper"' in html)


def test_no_inline_style_in_routes():
    """Sustainability gate: no inline <style> blocks in routes (except
    dashboard.py's .db-chart-wrap, which is tied to uplot and tracked
    as tech debt). New routes MUST put CSS in app.css."""
    import re
    routes_dir = os.path.join(os.path.dirname(__file__), '..', 'browse', 'routes')
    allowed = {'dashboard.py': ['.db-chart-wrap']}  # file → required tokens in the <style> block
    for fn in sorted(os.listdir(routes_dir)):
        if not fn.endswith('.py') or fn == '__init__.py':
            continue
        path = os.path.join(routes_dir, fn)
        content = open(path, encoding='utf-8').read()
        style_blocks = re.findall(r'<style[^>]*>.*?</style>', content, re.DOTALL | re.IGNORECASE)
        if not style_blocks:
            continue
        if fn not in allowed:
            raise AssertionError(f'{fn} contains inline <style> block — move CSS to app.css. Found: {style_blocks[0][:80]}...')
        for block in style_blocks:
            for required in allowed[fn]:
                if required not in block:
                    raise AssertionError(f'{fn} <style> block missing whitelisted token {required!r}')
    test("no inline <style> in routes (except dashboard.py whitelist)", True)


def run_all_tests() -> int:
    print("=== tests/test_ui_components.py ===")

    # Unit tests
    test_stat_grid()
    test_data_table()
    test_data_table_escapes_headers()
    test_data_table_preserves_cell_html()
    test_banner_variants()
    test_empty_state_shape()
    test_badge_variant_clamp()
    test_page_header_level_param()
    test_page_header_css_classes_exist()
    test_page_header_shape()
    test_card_shape()
    test_no_inline_style_in_routes()

    # Integration tests
    db = _make_test_db()
    server, host, port = _start_server(db)
    try:
        run_integration_tests(host, port)
    finally:
        server.shutdown()

    print(f"\n{'='*40}")
    print(f"PASSED: {_PASS}  FAILED: {_FAIL}")
    return _FAIL


if __name__ == "__main__":
    sys.exit(run_all_tests())
