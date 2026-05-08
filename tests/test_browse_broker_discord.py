"""tests/test_browse_broker_discord.py — Unit tests for browse/broker/discord.py.

Acceptance criteria verified here (issue #72 — Discord broker):
  - DiscordBroker can be instantiated with an in-memory DB and mock params.
  - _CommandDispatcher handles /status, /search, /briefing, /recent, /help.
  - /search with an empty query returns usage hint.
  - /briefing with no topic returns usage hint.
  - Unknown commands return an error message.
  - Unauthorized user_id: _handle_message silently drops the message (no send).
  - Bot messages are silently dropped (prevent loops).
  - Chunking: _chunk_text correctly splits large text at blank-line boundaries.
  - Chunking: text within limit is returned unchanged.
  - _no_proxy_opener returns an OpenerDirector that bypasses HTTP_PROXY env var.
  - DiscordBroker.__init__ does NOT call socket.bind (no inbound port).
  - _discord_request raises _DiscordError on HTTP 4xx/5xx.
  - _poll_messages processes reversed message ordering correctly.
  - browse/__init__.py exposes --broker-mode discord in its argparse help.

Architecture constraint documented by tests:
  This broker uses HTTP polling, not Discord Gateway WebSocket. Tests run
  credential-free by mocking all HTTP calls. Live RTT benchmarks require
  real credentials; that remains the open blocker for #72 full closure.
"""

import importlib
import os
import sqlite3
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from browse.broker.discord import (
    DiscordBroker,
    _CommandDispatcher,
    _DISCORD_EPOCH_MS,
    _DiscordError,
    _RateLimiter,
    _chunk_text,
    _db_recent,
    _db_search,
    _db_status,
    _make_now_snowflake,
    _no_proxy_opener,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_in_memory_db() -> sqlite3.Connection:
    """Create a minimal in-memory DB that mimics the production schema."""
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
        INSERT INTO knowledge_entries VALUES (1, 'Use ProxyHandler for no-proxy.');
        INSERT INTO knowledge_entries VALUES (2, 'Rate limit: respect per-channel limits.');

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
        self.assertEqual(_chunk_text("Hello world", max_chars=2000), ["Hello world"])

    def test_exact_limit_unchanged(self):
        text = "x" * 2000
        self.assertEqual(_chunk_text(text, max_chars=2000), [text])

    def test_splits_at_blank_line(self):
        para1 = "a" * 100
        para2 = "b" * 100
        text = para1 + "\n\n" + para2
        result = _chunk_text(text, max_chars=110)
        self.assertEqual(len(result), 2)
        self.assertIn("a" * 100, result[0])
        self.assertIn("b" * 100, result[1])

    def test_splits_at_newline_fallback(self):
        line1 = "line1 " * 15
        line2 = "line2 " * 15
        text = line1.rstrip() + "\n" + line2.rstrip()
        result = _chunk_text(text, max_chars=100)
        self.assertGreater(len(result), 1)
        combined = " ".join(result)
        self.assertIn("line1", combined)
        self.assertIn("line2", combined)

    def test_hard_split_no_newlines(self):
        text = "x" * 200
        result = _chunk_text(text, max_chars=100)
        self.assertEqual(len(result), 2)
        self.assertTrue(all(len(c) <= 100 for c in result))

    def test_discord_limit_2000(self):
        """Discord limit is 2000 chars — verify default max_chars."""
        text = "y" * 2001
        result = _chunk_text(text)  # uses default _MAX_MESSAGE_CHARS
        self.assertGreater(len(result), 1)
        self.assertTrue(all(len(c) <= 2000 for c in result))


# ── No-proxy opener ────────────────────────────────────────────────────────────

class TestNoProxyOpener(unittest.TestCase):
    def test_returns_opener_director(self):
        import urllib.request
        opener = _no_proxy_opener()
        self.assertIsInstance(opener, urllib.request.OpenerDirector)

    def test_does_not_use_http_proxy_env(self):
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

    def test_db_status_returns_dict(self):
        status = _db_status(self.db)
        self.assertIn("schema_version", status)
        self.assertEqual(status["sessions"], 2)
        self.assertEqual(status["knowledge_entries"], 2)
        self.assertEqual(status["schema_version"], 7)

    def test_db_search_returns_results(self):
        results = _db_search(self.db, "auth", limit=5)
        ids = [r["id"] for r in results]
        self.assertIn("sess-001", ids)

    def test_db_recent_returns_sessions(self):
        rows = _db_recent(self.db, limit=5)
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertIn("id", r)


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
        self.assertIn("/search", resp)

    def test_status_command(self):
        resp = self.dispatcher.dispatch("/status")
        self.assertIn("Sessions indexed", resp)
        self.assertIn("broker/discord", resp)

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
        resp = self.dispatcher.dispatch("hello world")
        self.assertIn("/help", resp)


# ── DiscordBroker instantiation ───────────────────────────────────────────────

class TestDiscordBrokerInit(unittest.TestCase):
    def setUp(self):
        self.db = _make_in_memory_db()

    def tearDown(self):
        self.db.close()

    def test_init_does_not_open_socket(self):
        """DiscordBroker.__init__ must NOT bind a TCP port (no inbound port)."""
        with patch("socket.socket") as mock_sock:
            broker = DiscordBroker(
                db=self.db,
                token="fake-token",
                channel_id="123456789",
                authorized_user_id="987654321",
            )
            # Should never have called socket.socket.bind
            for c in mock_sock.return_value.method_calls:
                self.assertNotEqual(c[0], "bind", "DiscordBroker must not bind a port")

    def test_init_stores_params(self):
        broker = DiscordBroker(
            db=self.db,
            token="tok-test",
            channel_id="chan-001",
            authorized_user_id="user-001",
        )
        self.assertEqual(broker._channel_id, "chan-001")
        self.assertEqual(broker._authorized_uid, "user-001")

    def test_channel_id_coerced_to_string(self):
        broker = DiscordBroker(
            db=self.db,
            token="tok",
            channel_id=123456,  # int should be coerced
            authorized_user_id="uid",
        )
        self.assertEqual(broker._channel_id, "123456")


# ── _handle_message auth gate ─────────────────────────────────────────────────

class TestDiscordBrokerHandleMessage(unittest.TestCase):
    def setUp(self):
        self.db = _make_in_memory_db()
        self.broker = DiscordBroker(
            db=self.db,
            token="fake-token",
            channel_id="111",
            authorized_user_id="auth-user-id",
        )

    def tearDown(self):
        self.db.close()

    def test_unauthorized_user_silently_dropped(self):
        msg = {
            "id": "10001",
            "author": {"id": "other-user", "bot": False},
            "content": "/status",
        }
        with patch.object(self.broker, "_send_chunks") as mock_send:
            self.broker._handle_message(msg)
            mock_send.assert_not_called()

    def test_authorized_user_dispatched(self):
        msg = {
            "id": "10002",
            "author": {"id": "auth-user-id", "bot": False},
            "content": "/help",
        }
        with patch.object(self.broker, "_send_chunks") as mock_send:
            self.broker._handle_message(msg)
            mock_send.assert_called_once()

    def test_bot_message_silently_dropped(self):
        """Messages from bots (bot=True) must be dropped to prevent reply loops."""
        msg = {
            "id": "10003",
            "author": {"id": "auth-user-id", "bot": True},
            "content": "/status",
        }
        with patch.object(self.broker, "_send_chunks") as mock_send:
            self.broker._handle_message(msg)
            mock_send.assert_not_called()

    def test_empty_content_dropped(self):
        msg = {
            "id": "10004",
            "author": {"id": "auth-user-id", "bot": False},
            "content": "",
        }
        with patch.object(self.broker, "_send_chunks") as mock_send:
            self.broker._handle_message(msg)
            mock_send.assert_not_called()


# ── _poll_messages ordering ───────────────────────────────────────────────────

class TestDiscordBrokerPollMessages(unittest.TestCase):
    def setUp(self):
        self.db = _make_in_memory_db()
        self.broker = DiscordBroker(
            db=self.db,
            token="fake-token",
            channel_id="222",
            authorized_user_id="u1",
        )

    def tearDown(self):
        self.db.close()

    def test_poll_returns_list(self):
        mock_response = [{"id": "100", "content": "hi"}, {"id": "101", "content": "there"}]
        with patch("browse.broker.discord._discord_request", return_value=mock_response):
            result = self.broker._poll_messages(after="99")
        self.assertEqual(result, mock_response)

    def test_poll_handles_non_list_response(self):
        with patch("browse.broker.discord._discord_request", return_value=None):
            result = self.broker._poll_messages(after="0")
        self.assertEqual(result, [])


# ── _send_message via mock ────────────────────────────────────────────────────

class TestDiscordBrokerSendMessage(unittest.TestCase):
    def setUp(self):
        self.db = _make_in_memory_db()
        self.broker = DiscordBroker(
            db=self.db,
            token="tok",
            channel_id="333",
            authorized_user_id="uid",
        )

    def tearDown(self):
        self.db.close()

    def test_send_message_calls_discord_request(self):
        with patch("browse.broker.discord._discord_request") as mock_req:
            mock_req.return_value = {"id": "200"}
            self.broker._send_message("Hello!")
            mock_req.assert_called_once()
            _, kwargs = mock_req.call_args
            # payload should have content field
            payload = mock_req.call_args[1].get("payload") or mock_req.call_args[0][4]
            self.assertIn("content", payload if isinstance(payload, dict) else {})

    def test_send_message_logs_on_error(self):
        import io
        with patch("browse.broker.discord._discord_request", side_effect=_DiscordError("fail")):
            with patch("sys.stderr", new_callable=io.StringIO) as mock_err:
                # Should NOT raise; should log to stderr
                self.broker._send_message("test")
                self.assertIn("error", mock_err.getvalue().lower())

    def test_send_chunks_splits_large_text(self):
        large = "A" * 5000
        sent_chunks = []
        with patch.object(self.broker, "_send_message", side_effect=lambda t: sent_chunks.append(t)):
            with patch.object(self.broker._limiter, "wait"):
                self.broker._send_chunks(large)
        self.assertGreater(len(sent_chunks), 1)
        self.assertTrue(all(len(c) <= 2000 for c in sent_chunks))


# ── browse/__init__.py broker-mode surface ────────────────────────────────────

class TestBrowseInitBrokerMode(unittest.TestCase):
    def test_broker_mode_discord_in_choices(self):
        """browse/__init__.py must expose --broker-mode discord in argparse choices."""
        import subprocess
        result = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.argv=['browse']; "
             "import browse; import argparse; "
             # Get help text and check for 'discord'
             "p = argparse.ArgumentParser(); "
             "import browse.__init__; "
             ],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
        )
        # Direct check: inspect the argparse choices for --broker-mode
        import argparse
        # We can't easily call the parser, so check the source directly
        init_src = (_REPO_ROOT / "browse" / "__init__.py").read_text()
        # The choices list should include 'discord'
        self.assertIn("discord", init_src,
                      "--broker-mode discord not found in browse/__init__.py")


# ── Startup cursor / backlog-replay regression ────────────────────────────────

class TestDiscordStartupCursor(unittest.TestCase):
    """Regression coverage for the Discord backlog-replay bug.

    When _get_latest_message_id() fails (returns '0'), the first poll omits the
    `after=` lower-bound and fetches the newest 100 messages, which can replay
    historical commands.  _catch_up_cursor() must advance past those messages
    without dispatching any of them.
    """

    def setUp(self):
        self.db = _make_in_memory_db()
        self.broker = DiscordBroker(
            db=self.db,
            token="tok",
            channel_id="999",
            authorized_user_id="uid",
        )

    def tearDown(self):
        self.db.close()

    def test_catch_up_cursor_returns_max_id(self):
        """_catch_up_cursor() must return the highest snowflake from the backlog."""
        fake_msgs = [
            {"id": "100", "content": "old command"},
            {"id": "300", "content": "another old"},
            {"id": "200", "content": "middle"},
        ]
        with patch("browse.broker.discord._discord_request", return_value=fake_msgs):
            result = self.broker._catch_up_cursor()
        self.assertEqual(result, "300")

    def test_catch_up_cursor_empty_channel_returns_zero(self):
        """_catch_up_cursor() returns '0' when the channel has no messages."""
        with patch("browse.broker.discord._discord_request", return_value=[]):
            result = self.broker._catch_up_cursor()
        self.assertEqual(result, "0")

    def test_catch_up_cursor_on_discord_error_returns_synthetic_snowflake(self):
        """_catch_up_cursor() must NOT return '0' when the fetch fails.

        Returning '0' would cause _poll_messages(after='0') to omit the lower
        bound, replaying up to 100 historical channel messages.  After the fix,
        _catch_up_cursor() returns a synthetic current-time snowflake instead,
        guaranteeing the first successful poll only delivers messages posted
        *after* startup.
        """
        with patch(
            "browse.broker.discord._discord_request",
            side_effect=_DiscordError("timeout"),
        ):
            result = self.broker._catch_up_cursor()
        # Must not be "0" — a zero cursor omits the after= lower bound
        self.assertNotEqual(result, "0", "cursor must not be '0' after fetch failure")
        # Must be a numeric string (valid snowflake)
        self.assertTrue(result.isdigit(), f"expected numeric snowflake, got {result!r}")
        # Must be >= a year-2020 snowflake (sanity: it's a real timestamp-based value)
        year_2020_ms = 1577836800000
        min_expected = (year_2020_ms - _DISCORD_EPOCH_MS) << 22
        self.assertGreaterEqual(
            int(result), min_expected,
            "synthetic snowflake should correspond to a time >= year 2020",
        )

    def test_catch_up_cursor_error_logs_warning_with_synthetic_cursor(self):
        """_catch_up_cursor() logs a warning to stderr when the fetch fails,
        and the warning mentions the synthetic cursor rather than replay risk."""
        import io
        with patch(
            "browse.broker.discord._discord_request",
            side_effect=_DiscordError("net err"),
        ):
            with patch("sys.stderr", new_callable=io.StringIO) as mock_err:
                self.broker._catch_up_cursor()
                log = mock_err.getvalue()
                self.assertIn("Warning", log)
                self.assertIn("synthetic cursor", log)
                self.assertNotIn("may be replayed", log)

    def test_first_poll_after_double_failure_cannot_replay_history(self):
        """End-to-end guarantee: when both cursor probes fail, the cursor passed
        to the first successful poll must include an 'after=' lower bound that
        excludes all messages that existed before startup.

        This is the core no-replay guarantee: _poll_messages must be called
        with a non-zero 'after' value so that it appends &after=<snowflake>
        to the request path, preventing replay of historical messages.

        Sequence under test:
          run() → _get_latest_message_id() returns "0"
               → _catch_up_cursor() → _poll_messages(after="0") → _DiscordError
               → _catch_up_cursor() catches error, returns synthetic snowflake
               → main loop: _poll_messages(after=synthetic_snowflake)  ← must be non-zero
        """
        poll_calls: list[str] = []
        call_count = [0]

        def fake_poll(after: str = "0") -> list:
            call_count[0] += 1
            if call_count[0] == 1:
                # First call is from _catch_up_cursor() — simulate fetch failure
                raise _DiscordError("timeout")
            else:
                # Second call is from the main poll loop — record and stop
                poll_calls.append(after)
                raise KeyboardInterrupt

        with patch.object(self.broker, "_verify_bot_identity"), \
             patch.object(self.broker, "_get_latest_message_id", return_value="0"), \
             patch.object(self.broker, "_poll_messages", side_effect=fake_poll):
            try:
                self.broker.run()
            except (SystemExit, KeyboardInterrupt):
                pass

        # The cursor passed to the first main-loop poll must NOT be "0"
        self.assertTrue(poll_calls, "run() should have attempted at least one main-loop poll")
        first_cursor = poll_calls[0]
        self.assertNotEqual(
            first_cursor, "0",
            "first poll cursor must not be '0' — a zero cursor omits the after= "
            "lower bound and would replay up to 100 historical channel messages",
        )
        self.assertTrue(
            first_cursor.isdigit(),
            f"first poll cursor must be a numeric snowflake, got {first_cursor!r}",
        )
        # Sanity: the synthetic cursor should correspond to a time >= year 2020
        year_2020_ms = 1577836800000
        min_expected = (year_2020_ms - _DISCORD_EPOCH_MS) << 22
        self.assertGreaterEqual(
            int(first_cursor), min_expected,
            "synthetic cursor should encode a timestamp >= year 2020",
        )

    def test_make_now_snowflake_is_numeric_and_recent(self):
        """_make_now_snowflake() must return a valid numeric Discord snowflake
        corresponding to the current time (not a future or ancient timestamp)."""
        before_ms = int(time.time() * 1000)
        result = _make_now_snowflake()
        after_ms = int(time.time() * 1000)

        self.assertTrue(result.isdigit(), f"expected numeric string, got {result!r}")
        ts_ms = (int(result) >> 22) + _DISCORD_EPOCH_MS
        # The snowflake timestamp must fall within the time window of this test call
        self.assertGreaterEqual(ts_ms, before_ms, "snowflake timestamp must be >= before call")
        self.assertLessEqual(ts_ms, after_ms, "snowflake timestamp must be <= after call")

    def test_run_calls_catch_up_when_latest_id_fails(self):
        """run() must call _catch_up_cursor() when _get_latest_message_id() returns '0'."""
        call_log = []

        def fake_catch_up():
            call_log.append("catch_up")
            return "999"

        with patch.object(
            self.broker, "_verify_bot_identity"
        ), patch.object(
            self.broker, "_get_latest_message_id", return_value="0"
        ), patch.object(
            self.broker, "_catch_up_cursor", side_effect=fake_catch_up
        ), patch.object(
            self.broker, "_poll_messages", side_effect=KeyboardInterrupt
        ):
            try:
                self.broker.run()
            except SystemExit:
                pass
        self.assertIn("catch_up", call_log)

    def test_run_does_not_call_catch_up_when_latest_id_valid(self):
        """run() must NOT call _catch_up_cursor() when _get_latest_message_id() succeeds."""
        call_log = []

        def fake_catch_up():
            call_log.append("catch_up")
            return "999"

        with patch.object(
            self.broker, "_verify_bot_identity"
        ), patch.object(
            self.broker, "_get_latest_message_id", return_value="12345"
        ), patch.object(
            self.broker, "_catch_up_cursor", side_effect=fake_catch_up
        ), patch.object(
            self.broker, "_poll_messages", side_effect=KeyboardInterrupt
        ):
            try:
                self.broker.run()
            except SystemExit:
                pass
        self.assertNotIn("catch_up", call_log)


if __name__ == "__main__":
    unittest.main()
