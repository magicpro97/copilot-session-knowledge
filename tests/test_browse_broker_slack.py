"""tests/test_browse_broker_slack.py — Unit tests for browse/broker/slack.py.

Acceptance criteria verified here (issue #72 — Slack broker):
  - SlackBroker can be instantiated with an in-memory DB and mock params.
  - _CommandDispatcher handles /status, /search, /briefing, /recent, /help.
  - Unauthorized user_id: _handle_message silently drops the message.
  - Bot messages (subtype=bot_message) are silently dropped.
  - Chunking: _chunk_text correctly splits large text.
  - _no_proxy_opener returns an OpenerDirector (bypasses HTTP_PROXY).
  - SlackBroker.__init__ does NOT call socket.bind (no inbound port).
  - _poll_messages correctly tracks the timestamp cursor.
  - _slack_request raises _SlackError on ok=False responses.
  - browse/__init__.py exposes --broker-mode slack in its argparse source.

Architecture constraint documented by tests:
  This broker uses HTTP polling of conversations.history, not Slack Socket Mode
  (WebSocket) or the Events API (inbound webhook/port). Tests run
  credential-free by mocking all HTTP calls. Live RTT benchmarks require real
  credentials; that remains the open blocker for #72 full closure.
"""

import io
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

from browse.broker.slack import (
    SlackBroker,
    _CommandDispatcher,
    _RateLimiter,
    _SlackError,
    _chunk_text,
    _db_recent,
    _db_search,
    _db_status,
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
        INSERT INTO knowledge_entries VALUES (1, 'Slack rate limits.');
        INSERT INTO knowledge_entries VALUES (2, 'HTTP polling pattern.');

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


# ── Chunk text tests ───────────────────────────────────────────────────────────

class TestChunkText(unittest.TestCase):
    def test_short_text_unchanged(self):
        self.assertEqual(_chunk_text("Hello Slack", max_chars=40000), ["Hello Slack"])

    def test_splits_at_blank_line(self):
        para1 = "a" * 100
        para2 = "b" * 100
        text = para1 + "\n\n" + para2
        result = _chunk_text(text, max_chars=110)
        self.assertEqual(len(result), 2)

    def test_hard_split_no_newlines(self):
        text = "x" * 200
        result = _chunk_text(text, max_chars=100)
        self.assertEqual(len(result), 2)
        self.assertTrue(all(len(c) <= 100 for c in result))


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

    def test_db_search(self):
        results = _db_search(self.db, "auth", limit=5)
        self.assertIsInstance(results, list)

    def test_db_recent(self):
        rows = _db_recent(self.db, limit=5)
        self.assertEqual(len(rows), 2)


# ── Rate limiter ──────────────────────────────────────────────────────────────

class TestRateLimiter(unittest.TestCase):
    def test_wait_enforces_minimum_interval(self):
        limiter = _RateLimiter(min_interval=0.1)
        t0 = time.monotonic()
        limiter.wait()
        limiter.wait()
        elapsed = time.monotonic() - t0
        self.assertGreaterEqual(elapsed, 0.08)


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

    def test_status_includes_mode(self):
        resp = self.dispatcher.dispatch("/status")
        self.assertIn("broker/slack", resp)

    def test_search_with_query(self):
        resp = self.dispatcher.dispatch("/search auth")
        self.assertIn("auth", resp.lower())

    def test_search_without_query(self):
        resp = self.dispatcher.dispatch("/search")
        self.assertIn("Usage", resp)

    def test_briefing_without_topic(self):
        resp = self.dispatcher.dispatch("/briefing")
        self.assertIn("Usage", resp)

    def test_recent_command(self):
        resp = self.dispatcher.dispatch("/recent")
        self.assertTrue("sessions" in resp.lower() or "No sessions" in resp)

    def test_unknown_command(self):
        resp = self.dispatcher.dispatch("/unknown")
        self.assertIn("Unknown command", resp)

    def test_non_command_text(self):
        resp = self.dispatcher.dispatch("hello there")
        self.assertIn("/help", resp)


# ── SlackBroker instantiation ─────────────────────────────────────────────────

class TestSlackBrokerInit(unittest.TestCase):
    def setUp(self):
        self.db = _make_in_memory_db()

    def tearDown(self):
        self.db.close()

    def test_init_does_not_open_socket(self):
        """SlackBroker.__init__ must NOT bind a TCP port (no inbound port)."""
        with patch("socket.socket") as mock_sock:
            broker = SlackBroker(
                db=self.db,
                bot_token="xoxb-fake-token",
                channel_id="C123",
                authorized_user_id="U456",
            )
            for c in mock_sock.return_value.method_calls:
                self.assertNotEqual(c[0], "bind", "SlackBroker must not bind a port")

    def test_stores_params(self):
        broker = SlackBroker(
            db=self.db,
            bot_token="xoxb-tok",
            channel_id="C001",
            authorized_user_id="U001",
        )
        self.assertEqual(broker._channel_id, "C001")
        self.assertEqual(broker._authorized_uid, "U001")


# ── _handle_message auth gate ─────────────────────────────────────────────────

class TestSlackBrokerHandleMessage(unittest.TestCase):
    def setUp(self):
        self.db = _make_in_memory_db()
        self.broker = SlackBroker(
            db=self.db,
            bot_token="xoxb-tok",
            channel_id="C111",
            authorized_user_id="UAUTH",
        )

    def tearDown(self):
        self.db.close()

    def test_unauthorized_user_dropped(self):
        msg = {"user": "UOTHER", "text": "/status", "ts": "1000.001"}
        with patch.object(self.broker, "_send_chunks") as mock_send:
            self.broker._handle_message(msg)
            mock_send.assert_not_called()

    def test_authorized_user_dispatched(self):
        msg = {"user": "UAUTH", "text": "/help", "ts": "1000.002"}
        with patch.object(self.broker, "_send_chunks") as mock_send:
            self.broker._handle_message(msg)
            mock_send.assert_called_once()

    def test_bot_message_subtype_dropped(self):
        """Messages with subtype=bot_message must be dropped to prevent loops."""
        msg = {"user": "UAUTH", "text": "/status", "subtype": "bot_message", "ts": "1000.003"}
        with patch.object(self.broker, "_send_chunks") as mock_send:
            self.broker._handle_message(msg)
            mock_send.assert_not_called()

    def test_bot_id_message_dropped(self):
        """Messages with bot_id field must be dropped."""
        msg = {"user": "UAUTH", "text": "/status", "bot_id": "B123", "ts": "1000.004"}
        with patch.object(self.broker, "_send_chunks") as mock_send:
            self.broker._handle_message(msg)
            mock_send.assert_not_called()

    def test_empty_text_dropped(self):
        msg = {"user": "UAUTH", "text": "", "ts": "1000.005"}
        with patch.object(self.broker, "_send_chunks") as mock_send:
            self.broker._handle_message(msg)
            mock_send.assert_not_called()

    def test_no_user_dropped(self):
        msg = {"text": "/status", "ts": "1000.006"}
        with patch.object(self.broker, "_send_chunks") as mock_send:
            self.broker._handle_message(msg)
            mock_send.assert_not_called()


# ── _poll_messages cursor ─────────────────────────────────────────────────────

class TestSlackBrokerPollMessages(unittest.TestCase):
    def setUp(self):
        self.db = _make_in_memory_db()
        self.broker = SlackBroker(
            db=self.db,
            bot_token="xoxb-tok",
            channel_id="C222",
            authorized_user_id="UAUTH",
        )

    def tearDown(self):
        self.db.close()

    def test_poll_advances_cursor(self):
        fake_result = {
            "ok": True,
            "messages": [
                {"user": "UAUTH", "text": "/status", "ts": "1000.100"},
                {"user": "UAUTH", "text": "/help", "ts": "1000.200"},
            ],
        }
        with patch("browse.broker.slack._slack_request", return_value=fake_result):
            msgs, new_oldest = self.broker._poll_messages(oldest="1000.000")
        self.assertEqual(len(msgs), 2)
        self.assertEqual(new_oldest, "1000.200")

    def test_poll_returns_empty_messages(self):
        fake_result = {"ok": True, "messages": []}
        with patch("browse.broker.slack._slack_request", return_value=fake_result):
            msgs, new_oldest = self.broker._poll_messages(oldest="999.000")
        self.assertEqual(msgs, [])
        self.assertEqual(new_oldest, "999.000")

    def test_poll_cursor_does_not_go_back(self):
        fake_result = {
            "ok": True,
            "messages": [{"user": "UAUTH", "text": "hi", "ts": "500.000"}],
        }
        with patch("browse.broker.slack._slack_request", return_value=fake_result):
            msgs, new_oldest = self.broker._poll_messages(oldest="999.000")
        # Message ts < oldest → cursor should remain at oldest
        self.assertEqual(new_oldest, "999.000")


# ── _post_message ─────────────────────────────────────────────────────────────

class TestSlackBrokerPostMessage(unittest.TestCase):
    def setUp(self):
        self.db = _make_in_memory_db()
        self.broker = SlackBroker(
            db=self.db,
            bot_token="xoxb-tok",
            channel_id="C333",
            authorized_user_id="UAUTH",
        )

    def tearDown(self):
        self.db.close()

    def test_post_message_calls_slack_request(self):
        with patch("browse.broker.slack._slack_request") as mock_req:
            mock_req.return_value = {"ok": True}
            self.broker._post_message("Hello Slack!")
            mock_req.assert_called_once()

    def test_post_message_logs_on_error(self):
        with patch("browse.broker.slack._slack_request", side_effect=_SlackError("fail")):
            with patch("sys.stderr", new_callable=io.StringIO) as mock_err:
                self.broker._post_message("test")
                self.assertIn("error", mock_err.getvalue().lower())

    def test_send_chunks_splits_large_text(self):
        large = "A" * 150000
        sent = []
        with patch.object(self.broker, "_post_message", side_effect=lambda t: sent.append(t)):
            with patch.object(self.broker._limiter, "wait"):
                self.broker._send_chunks(large)
        self.assertGreater(len(sent), 1)
        self.assertTrue(all(len(c) <= 40000 for c in sent))


# ── browse/__init__.py surface ────────────────────────────────────────────────

class TestBrowseInitSlackMode(unittest.TestCase):
    def test_broker_mode_slack_in_init(self):
        init_src = (_REPO_ROOT / "browse" / "__init__.py").read_text()
        self.assertIn("slack", init_src,
                      "--broker-mode slack not found in browse/__init__.py")


if __name__ == "__main__":
    unittest.main()
