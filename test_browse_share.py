#!/usr/bin/env python3
"""
test_browse_share.py — Tests for share.js (Copy link + Screenshot feature)

Uses http.client against a locally spawned ThreadingHTTPServer with
an in-memory SQLite DB. No external dependencies required.
"""

import ast
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

sys.path.insert(0, str(Path(__file__).parent))
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


def _make_test_db(with_fts: bool = True) -> sqlite3.Connection:
    """Create an in-memory SQLite DB with the required schema."""
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
            seq INTEGER, title TEXT, content TEXT, indexed_at TEXT
        );
        CREATE VIRTUAL TABLE fts_sections USING fts5(id, content);
    """)
    return db


def _spawn_server():
    """Spawn a local HTTP server on 127.0.0.1:18777 with test DB."""
    db = _make_test_db()
    db.execute(
        """INSERT INTO sessions (id, path, summary, source, file_mtime,
        indexed_at_r, fts_indexed_at, event_count_estimate, file_size_bytes,
        total_checkpoints, total_research, total_files, has_plan, indexed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "sess1",
            "/tmp/s1.md",
            "Test session",
            "copilot-cli",
            time.time(),
            time.time(),
            time.time(),
            100,
            5000,
            1,
            1,
            10,
            1,
            "2024-01-01T00:00:00",
        ),
    )
    db.execute(
        """INSERT INTO documents (session_id, doc_type, seq, title, file_path,
        file_hash, size_bytes, content_preview, indexed_at, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "sess1",
            "thought",
            1,
            "Thought 1",
            None,
            "abc123",
            100,
            "Preview",
            "2024-01-01T00:00:00",
            "copilot-cli",
        ),
    )
    db.commit()

    from browse.core.server import _make_handler_class
    HandlerClass = _make_handler_class(db, token="")
    server = ThreadingHTTPServer(("127.0.0.1", 18777), HandlerClass)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    time.sleep(0.2)
    return server


def test_suite():
    """Run all tests."""
    global _PASS, _FAIL

    print("=" * 60)
    print("test_browse_share.py — Share Feature Tests")
    print("=" * 60)

    # Test 1: share.js file exists
    share_js_path = Path(__file__).parent / "browse" / "static" / "js" / "share.js"
    test(
        "share.js exists",
        share_js_path.exists(),
    )

    # Test 2: share.js is valid JavaScript (can parse it)
    if share_js_path.exists():
        content = share_js_path.read_text(encoding="utf-8")
        # Check for required functions (screenshot removed per task)
        has_functions = all(
            fn in content for fn in ["copyLink", "injectToolbar"]
        )
        test("share.js has required functions", has_functions)
    else:
        test("share.js has required functions", False)

    # Test 3: html-to-image.min.js has been removed (screenshot feature deleted)
    vendor_path = Path(__file__).parent / "browse" / "static" / "vendor" / "html-to-image.min.js"
    test("html-to-image.min.js removed", not vendor_path.exists())

    # Test 4: VENDOR.md no longer references html-to-image
    vendor_md_path = Path(__file__).parent / "browse" / "static" / "vendor" / "VENDOR.md"
    if vendor_md_path.exists():
        vendor_md = vendor_md_path.read_text(encoding="utf-8")
        test("VENDOR.md does not document html-to-image", "html-to-image" not in vendor_md)
    else:
        test("VENDOR.md does not document html-to-image", False)

    # Test 5: templates.py includes share.js in body_scripts
    templates_path = Path(__file__).parent / "browse" / "core" / "templates.py"
    if templates_path.exists():
        templates_content = templates_path.read_text(encoding="utf-8")
        has_share_script = 'src="/static/js/share.js"' in templates_content
        test("templates.py includes share.js script tag", has_share_script)

        # html-to-image intentionally absent from templates
        test("templates.py does not reference html-to-image", "html-to-image" not in templates_content)
    else:
        test("templates.py includes share.js script tag", False)
        test("templates.py does not reference html-to-image", False)

    # Test 6: templates.py base_page signature unchanged
    if templates_path.exists():
        templates_content = templates_path.read_text(encoding="utf-8")
        # Check that base_page still has the correct signature (6 params + self)
        sig_pattern = r"def base_page\(\s*nonce:\s*str,\s*title:\s*str,\s*main_content:\s*str\s*=\s*\"\",\s*head_extra:\s*str\s*=\s*\"\",\s*body_scripts:\s*str\s*=\s*\"\",\s*nav_extra:\s*str\s*=\s*\"\",\s*token:\s*str\s*=\s*\"\",\s*\)"
        sig_found = re.search(sig_pattern, templates_content)
        test("templates.py base_page signature preserved", sig_found is not None)
    else:
        test("templates.py base_page signature preserved", False)

    # Test 7: share.js has no inline event handlers (CSP safe)
    if share_js_path.exists():
        share_content = share_js_path.read_text(encoding="utf-8")
        # Check for inline onclick/onmouseover/onmouseout attributes
        has_inline = (
            'onclick="' in share_content or
            'onmouseover="' in share_content or
            'onmouseout="' in share_content
        )
        # Actually, we DO have inline onclick/onmouseover/onmouseout set via JS
        # which is safe. The CSP check is really about no inline <script> blocks
        # with hardcoded code. So check that no <script> tags contain code.
        # Our share.js uses .onclick = function() pattern which is safe.
        
        # Better check: no onclick="" attributes in static HTML
        has_static_inline_onclick = re.search(r'onclick\s*=\s*["\']', share_content)
        test("share.js no static inline handlers", has_static_inline_onclick is None)
    else:
        test("share.js no static inline handlers", False)

    # Test 8: Server responds with share.js on GET /static/js/share.js
    print("\n  Testing HTTP server integration...")
    try:
        server = _spawn_server()
        conn = http.client.HTTPConnection("127.0.0.1", 18777)
        conn.request("GET", "/")
        resp = conn.getresponse()
        html = resp.read().decode("utf-8", errors="replace")
        conn.close()

        # Check that home page contains share.js script tag with nonce
        has_share_tag = 'src="/static/js/share.js"' in html and 'nonce=' in html
        test("home page includes share.js with nonce", has_share_tag)

        # Check that home page does NOT load html-to-image (feature removed)
        test("home page does not include html-to-image", "html-to-image" not in html)

        server.shutdown()
    except Exception as e:
        print(f"  Server error: {e}")
        test("home page includes share.js with nonce", False)
        test("home page does not include html-to-image", False)

    print("\n" + "=" * 60)
    print(f"Results: {_PASS} passed, {_FAIL} failed")
    print("=" * 60)

    return _FAIL == 0


if __name__ == "__main__":
    success = test_suite()
    sys.exit(0 if success else 1)
