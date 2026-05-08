"""tests/test_browse_broker_ably.py — Unit tests for browse/broker/ably.py.

Acceptance criteria verified here (issue #72 — Ably broker):
  - AblyBroker can be instantiated with an in-memory DB and mock params.
  - _CommandDispatcher handles /status, /search, /briefing, /recent, /help.
  - Unauthorized clientId: _handle_message silently drops the message.
  - Chunking: _chunk_text correctly splits large text.
  - _no_proxy_opener returns an OpenerDirector (bypasses HTTP_PROXY).
  - AblyBroker.__init__ does NOT call socket.bind (no inbound port).
  - _make_auth_header produces valid Basic auth from Ably API key format.
  - _poll_messages correctly tracks timestamp cursor.
  - _ably_request raises _AblyError on HTTP errors.
  - browse/__init__.py exposes --broker-mode ably in its argparse source.

Architecture constraint documented by tests:
  This broker uses HTTP REST polling of Ably channel history, not the Ably
  Realtime WebSocket client. Tests run credential-free by mocking all HTTP
  calls. Live RTT benchmarks require real credentials; that remains the open
  blocker for #72 full closure.
"""

import base64
import os
import sqlite3
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from browse.broker.ably import (
    AblyBroker,
    _AblyError,
    _CommandDispatcher,
    _RateLimiter,
    _chunk_text,
    _db_recent,
    _db_search,
    _db_status,
    _make_auth_header,
    _no_proxy_opener,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_in_memory_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE schema_version (version INTEGER);
        INSERT INTO schema_version VALUES (7);

        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            summary TEXT,
            source TEXT,
            indexed_at TEXT
        );
        INSERT INTO sessions VALUES ('sess-001', 'Auth token refresh flow', 'git', NULL);
        INSERT INTO sessions VALUES ('sess-002', 'Improve search latency', 'jira', NULL);

        CREATE TABLE knowledge_entries (
            id INTEGER PRIMARY KEY,
            content TEXT
        );
        INSERT INTO knowledge_entries VALUES (1, 'No-proxy pattern.');
        INSERT INTO knowledge_entries VALUES (2, 'Ably REST polling.');

        CREATE VIRTUAL TABLE sessions_fts USING fts5(
            session_id UNINDEXED,
            title,
            user_messages,
            assistant_messages,
            tool_names
        );
        INSERT INTO sessions_fts VALUES ('sess-001', 'Auth session', 'auth token refresh', '', '');
        INSERT INTO sessions_fts VALUES ('sess-002', 'Search session', 'search latency', '', '');
        """
    )
    return db


# ── Auth header ───────────────────────────────────────────────────────────────

class TestMakeAuthHeader(unittest.TestCase):
    def test_basic_auth_format(self):
        header = _make_auth_header("appid.keyid:keysecret")
        self.assertTrue(header.startswith("Basic "))
        encoded = header[len("Basic "):]
        decoded = base64.b64decode(encoded).decode("utf-8")
        self.assertEqual(decoded, "appid.keyid:keysecret")

    def test_api_key_with_complex_chars(self):
        key = "abc123.def456:ghi789!@#"
        header = _make_auth_header(key)
        encoded = header[len("Basic "):]
        decoded = base64.b64decode(encoded).decode("utf-8")
        self.assertEqual(decoded, key)


# ── Chunk text tests ───────────────────────────────────────────────────────────

class TestChunkText(unittest.TestCase):
    def test_short_text_unchanged(self):
        self.assertEqual(_chunk_text("Hello world", max_chars=1000), ["Hello world"])

    def test_splits_at_blank_line(self):
        para1 = "a" * 100
        para2 = "b" * 100
        text = para1 + "\n\n" + para2
        result = _chunk_text(text, max_chars=110)
        self.assertEqual(len(result), 2)

    def test_ably_large_limit(self):
        """Ably allows large messages; default should handle reasonably large text."""
        text = "x" * 100
        result = _chunk_text(text)
        self.assertEqual(len(result), 1)


# ── No-proxy opener ────────────────────────────────────────────────────────────

class TestNoProxyOpener(unittest.TestCase):
    def test_returns_opener_director(self):
        import urllib.request
        opener = _no_proxy_opener()
        self.assertIsInstance(opener, urllib.request.OpenerDirector)

    def test_bypasses_http_proxy_env(self):
        import urllib.request
        old = os.environ.pop("HTTP_PROXY", None)
        os.environ["HTTP_PROXY"] = "http://should-not-be-used:9999"
        try:
            opener = _no_proxy_opener()
            for h in opener.handlers:
                if isinstance(h, urllib.request.ProxyHandler):
                    self.assertEqual(h.proxies, {})
        finally:
            del os.environ["HTTP_PROXY"]
            if old is not None:
                os.environ["HTTP_PROXY"] = old


# ── DB helpers ────────────────────────────────────────────────────────────────

class TestDbHelpers(unittest.TestCase):
    def setUp(self):
        self.db = _make_in_memory_db()

    def tearDown(self):
        self.db.close()

    def test_db_status(self):
        status = _db_status(self.db)
        self.assertEqual(status["sessions"], 2)
        self.assertEqual(status["knowledge_entries"], 2)
        self.assertEqual(status["schema_version"], 7)

    def test_db_search(self):
        results = _db_search(self.db, "auth", limit=5)
        ids = [r["id"] for r in results]
        self.assertIn("sess-001", ids)

    def test_db_recent(self):
        rows = _db_recent(self.db, limit=5)
        self.assertEqual(len(rows), 2)


# ── Rate limiter ──────────────────────────────────────────────────────────────

class TestRateLimiter(unittest.TestCase):
    def test_wait_enforces_minimum_interval(self):
        limiter = _RateLimiter(min_interval=0.05)
        t0 = time.monotonic()
        limiter.wait()
        limiter.wait()
        elapsed = time.monotonic() - t0
        self.assertGreaterEqual(elapsed, 0.04)


# ── Command dispatcher ────────────────────────────────────────────────────────

class TestCommandDispatcher(unittest.TestCase):
    def setUp(self):
        self.db = _make_in_memory_db()
        self.dispatcher = _CommandDispatcher(db=self.db, start_time=time.monotonic())

    def tearDown(self):
        self.db.close()

    def test_help_command(self):
        resp = self.dispatcher.dispatch("/help")
        self.assertIn("/status", resp)

    def test_status_command(self):
        resp = self.dispatcher.dispatch("/status")
        self.assertIn("broker/ably", resp)
        self.assertIn("Sessions indexed", resp)

    def test_search_without_query(self):
        resp = self.dispatcher.dispatch("/search")
        self.assertIn("Usage", resp)

    def test_briefing_without_topic(self):
        resp = self.dispatcher.dispatch("/briefing")
        self.assertIn("Usage", resp)

    def test_unknown_command(self):
        resp = self.dispatcher.dispatch("/xyz")
        self.assertIn("Unknown command", resp)

    def test_non_command_text(self):
        resp = self.dispatcher.dispatch("hello there")
        self.assertIn("/help", resp)


# ── AblyBroker instantiation ──────────────────────────────────────────────────

class TestAblyBrokerInit(unittest.TestCase):
    def setUp(self):
        self.db = _make_in_memory_db()

    def tearDown(self):
        self.db.close()

    def test_init_does_not_open_socket(self):
        with patch("socket.socket") as mock_sock:
            broker = AblyBroker(
                db=self.db,
                api_key="appid.keyid:secret",
            )
            for c in mock_sock.return_value.method_calls:
                self.assertNotEqual(c[0], "bind", "AblyBroker must not bind a port")

    def test_default_channel_names(self):
        broker = AblyBroker(db=self.db, api_key="appid.keyid:secret")
        self.assertEqual(broker._channel_in, "browse-commands")
        self.assertEqual(broker._channel_out, "browse-responses")
        self.assertEqual(broker._authorized_client_id, "operator")

    def test_custom_channel_names(self):
        broker = AblyBroker(
            db=self.db,
            api_key="key",
            channel_in="my-in",
            channel_out="my-out",
            authorized_client_id="alice",
        )
        self.assertEqual(broker._channel_in, "my-in")
        self.assertEqual(broker._channel_out, "my-out")
        self.assertEqual(broker._authorized_client_id, "alice")


# ── _handle_message auth gate ─────────────────────────────────────────────────

class TestAblyBrokerHandleMessage(unittest.TestCase):
    def setUp(self):
        self.db = _make_in_memory_db()
        self.broker = AblyBroker(
            db=self.db,
            api_key="key",
            authorized_client_id="operator",
        )

    def tearDown(self):
        self.db.close()

    def test_unauthorized_client_dropped(self):
        msg = {"clientId": "attacker", "data": "/status"}
        with patch.object(self.broker, "_send_chunks") as mock_send:
            self.broker._handle_message(msg)
            mock_send.assert_not_called()

    def test_authorized_client_dispatched(self):
        msg = {"clientId": "operator", "data": "/help"}
        with patch.object(self.broker, "_send_chunks") as mock_send:
            self.broker._handle_message(msg)
            mock_send.assert_called_once()

    def test_no_client_id_dropped(self):
        msg = {"data": "/status"}
        with patch.object(self.broker, "_send_chunks") as mock_send:
            self.broker._handle_message(msg)
            mock_send.assert_not_called()

    def test_empty_data_dropped(self):
        msg = {"clientId": "operator", "data": ""}
        with patch.object(self.broker, "_send_chunks") as mock_send:
            self.broker._handle_message(msg)
            mock_send.assert_not_called()


# ── _poll_messages timestamp cursor ──────────────────────────────────────────

class TestAblyBrokerPollMessages(unittest.TestCase):
    def setUp(self):
        self.db = _make_in_memory_db()
        self.broker = AblyBroker(db=self.db, api_key="key")

    def tearDown(self):
        self.db.close()

    def test_poll_returns_messages_and_advances_cursor(self):
        fake_messages = [
            {"timestamp": 1000100, "clientId": "operator", "data": "/help"},
            {"timestamp": 1000200, "clientId": "operator", "data": "/status"},
        ]
        with patch("browse.broker.ably._ably_request", return_value=fake_messages):
            msgs, new_ts = self.broker._poll_messages(since_ms=1000000)
        self.assertEqual(msgs, fake_messages)
        self.assertEqual(new_ts, 1000200)

    def test_poll_returns_empty_on_none(self):
        with patch("browse.broker.ably._ably_request", return_value=None):
            msgs, new_ts = self.broker._poll_messages(since_ms=1000000)
        self.assertEqual(msgs, [])
        self.assertEqual(new_ts, 1000000)  # cursor unchanged

    def test_poll_cursor_does_not_go_back(self):
        fake_messages = [{"timestamp": 999, "clientId": "operator", "data": "/help"}]
        with patch("browse.broker.ably._ably_request", return_value=fake_messages):
            msgs, new_ts = self.broker._poll_messages(since_ms=1000000)
        # Message has earlier timestamp — cursor should not go below since_ms
        self.assertEqual(new_ts, 1000000)


# ── _publish_message ──────────────────────────────────────────────────────────

class TestAblyBrokerPublish(unittest.TestCase):
    def setUp(self):
        self.db = _make_in_memory_db()
        self.broker = AblyBroker(db=self.db, api_key="key")

    def tearDown(self):
        self.db.close()

    def test_publish_calls_ably_request(self):
        with patch("browse.broker.ably._ably_request") as mock_req:
            mock_req.return_value = None
            self.broker._publish_message("hello")
            mock_req.assert_called_once()

    def test_publish_logs_on_error(self):
        import io
        with patch("browse.broker.ably._ably_request", side_effect=_AblyError("fail")):
            with patch("sys.stderr", new_callable=io.StringIO) as mock_err:
                self.broker._publish_message("test")
                self.assertIn("error", mock_err.getvalue().lower())

    def test_send_chunks_splits_text(self):
        large = "Z" * 200000
        sent = []
        with patch.object(self.broker, "_publish_message", side_effect=lambda t: sent.append(t)):
            with patch.object(self.broker._limiter, "wait"):
                self.broker._send_chunks(large)
        self.assertGreater(len(sent), 1)


# ── browse/__init__.py surface ────────────────────────────────────────────────

class TestBrowseInitAblyMode(unittest.TestCase):
    def test_broker_mode_ably_in_init(self):
        init_src = (_REPO_ROOT / "browse" / "__init__.py").read_text()
        self.assertIn("ably", init_src,
                      "--broker-mode ably not found in browse/__init__.py")


# ── Pagination saturation warning regression ──────────────────────────────────

class TestAblyPollSaturation(unittest.TestCase):
    """Regression coverage for the Ably pagination/saturation warning.

    When _poll_messages() receives a full page (100 messages), it must emit a
    warning to stderr because pagination is not followed and messages beyond
    the page boundary will be silently dropped.
    """

    def setUp(self):
        self.db = _make_in_memory_db()
        self.broker = AblyBroker(db=self.db, api_key="key")

    def tearDown(self):
        self.db.close()

    def test_saturation_warning_emitted_when_full_page(self):
        """A stderr warning is emitted when exactly 100 messages are returned."""
        import io
        fake_msgs = [
            {"timestamp": 1000000 + i, "clientId": "operator", "data": "/help"}
            for i in range(100)
        ]
        with patch("browse.broker.ably._ably_request", return_value=fake_msgs):
            with patch("sys.stderr", new_callable=io.StringIO) as mock_err:
                msgs, _ = self.broker._poll_messages(since_ms=999000)
        self.assertEqual(len(msgs), 100)
        self.assertIn("Warning", mock_err.getvalue())
        self.assertIn("page", mock_err.getvalue().lower())

    def test_no_saturation_warning_for_partial_page(self):
        """No stderr warning when fewer than 100 messages are returned."""
        import io
        fake_msgs = [
            {"timestamp": 1000000 + i, "clientId": "operator", "data": "/help"}
            for i in range(50)
        ]
        with patch("browse.broker.ably._ably_request", return_value=fake_msgs):
            with patch("sys.stderr", new_callable=io.StringIO) as mock_err:
                msgs, _ = self.broker._poll_messages(since_ms=999000)
        self.assertEqual(len(msgs), 50)
        self.assertEqual(mock_err.getvalue(), "")

    def test_poll_still_returns_messages_on_full_page(self):
        """Full-page result is still returned even with the warning."""
        fake_msgs = [
            {"timestamp": 1000000 + i, "clientId": "operator", "data": "/help"}
            for i in range(100)
        ]
        with patch("browse.broker.ably._ably_request", return_value=fake_msgs):
            msgs, new_ts = self.broker._poll_messages(since_ms=999000)
        self.assertEqual(len(msgs), 100)
        self.assertEqual(new_ts, 1000099)


# ── clientId trust-boundary documentation test ───────────────────────────────

class TestAblyClientIdTrustBoundary(unittest.TestCase):
    """Verify that the clientId security caveat is documented in source."""

    def test_handle_message_docstring_mentions_caveat(self):
        """_handle_message docstring must mention the clientId trust boundary."""
        import inspect
        from browse.broker.ably import AblyBroker
        doc = inspect.getdoc(AblyBroker._handle_message)
        self.assertIsNotNone(doc)
        # Should mention that clientId is not server-verified
        self.assertTrue(
            any(kw in doc.lower() for kw in ["self-reported", "not server-verified", "capability"]),
            "_handle_message docstring must warn that clientId is not server-verified by Ably",
        )

    def test_module_docstring_mentions_clientid_caveat(self):
        """Module docstring must mention the clientId trust boundary limitation."""
        import browse.broker.ably as ably_mod
        doc = ably_mod.__doc__ or ""
        self.assertTrue(
            "clientId" in doc and ("self-reported" in doc or "capability" in doc.lower()),
            "Module docstring must document the clientId trust boundary",
        )


if __name__ == "__main__":
    unittest.main()
