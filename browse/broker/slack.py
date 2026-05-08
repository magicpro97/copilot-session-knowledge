"""browse/broker/slack.py — Slack HTTP-polling broker for browse.py (issue #72).

Architecture (§5 Outbound Control-Bus Mode):
  - Polls Slack Web API conversations.history using stdlib urllib
    (NO WebSocket; NO inbound port opened).
  - Dispatches /status, /search, /briefing, /recent, /help commands posted to
    the configured Slack channel by the authorized user.
  - Auth: silently drops every message from any user_id != authorized_user_id.
  - Rate limiting: enforces Slack's 1 msg/s per method Tier-3 rate limit.
  - Chunks replies at blank-line / newline boundaries (Slack ~40 KB limit).
  - Proxy bypass: uses ProxyHandler({}) so tunnel-hostile proxy env vars do NOT
    intercept outbound Slack API calls.

Architecture constraint (IMPORTANT):
  This broker uses HTTP polling of the Slack conversations.history endpoint
  (GET /conversations.history?channel={id}&oldest={ts}) rather than Slack
  Socket Mode (WebSocket) or the Events API (inbound webhook/port).

  Polling approach: stdlib-compatible, no third-party packages, no inbound port.
  Poll latency: ~2 s versus <500 ms for Socket Mode.

  If sub-second latency is required, Slack Socket Mode (WebSocket) requires
  the 'slack_bolt' or 'websocket-client' PyPI package (non-stdlib).
  The Slack Events API requires an inbound HTTPS port (violates "no inbound port"
  architecture constraint).

Remaining blockers for #72 full closure:
  1. credentials — BROWSE_BROKER_SLACK_BOT_TOKEN (xoxb-... OAuth token),
                   BROWSE_BROKER_SLACK_CHANNEL_ID,
                   BROWSE_BROKER_SLACK_AUTHORIZED_USER_ID
  2. Maintainer must measure median + p95 RTT over ≥100 messages with real
     credentials on a tunnel-hostile (FPT) network.
  3. FPT-simulation run with real credentials has not been performed.

Entry point:
  broker = SlackBroker(db=db, bot_token=token, channel_id=channel_id,
                        authorized_user_id=uid)
  broker.run()  # blocks; raises KeyboardInterrupt on Ctrl-C

Environment (read by browse/__init__.py, passed here):
  BROWSE_BROKER_SLACK_BOT_TOKEN          — Slack bot OAuth token (xoxb-..., required)
  BROWSE_BROKER_SLACK_CHANNEL_ID         — Slack channel ID to poll (required)
  BROWSE_BROKER_SLACK_AUTHORIZED_USER_ID — single string Slack user ID (required)

Stdlib-only: no third-party packages. No WebSocket. No inbound port.
"""

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

# ── Constants ─────────────────────────────────────────────────────────────────

_SLACK_API_BASE = "https://slack.com/api"
_POLL_INTERVAL = 2.0  # seconds between conversations.history polls
_MIN_SEND_INTERVAL = 1.05  # Slack Tier-3: 1 msg/s per method + margin
_MAX_MESSAGE_CHARS = 40000  # Slack approximate message size limit
_SEARCH_LIMIT = 5
_RECENT_LIMIT = 10
_HELP_TEXT = f"""\
*Hindsight Browse — available commands*

/status — daemon status (uptime, session count, DB version)
/search <query> — search the knowledge base (top {_SEARCH_LIMIT} results)
/briefing <topic> — run briefing for a topic
/recent — list the {_RECENT_LIMIT} most recent sessions
/help — this message

_All commands are read-only. Auth: single-user whitelist._
"""


# ── Proxy-bypass opener ────────────────────────────────────────────────────────


def _no_proxy_opener() -> urllib.request.OpenerDirector:
    """Return a urllib opener that bypasses HTTP(S)_PROXY environment variables."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ── Slack API helpers ──────────────────────────────────────────────────────────


class _SlackError(Exception):
    """Wraps non-200 / parse errors or Slack API ok=false responses."""


def _slack_request(
    opener: urllib.request.OpenerDirector,
    bot_token: str,
    method: str,
    endpoint: str,
    payload: dict | None = None,
    params: dict | None = None,
    timeout: int = 15,
) -> dict:
    """Send a Slack Web API request; return the parsed JSON body.

    method   — 'GET' or 'POST'
    endpoint — Slack API method name, e.g. 'conversations.history'
    params   — query-string params for GET requests
    payload  — JSON body for POST requests
    Raises _SlackError on HTTP errors or if ok==False.
    """
    url = f"{_SLACK_API_BASE}/{endpoint}"
    headers = {
        "Authorization": f"Bearer {bot_token}",
        "Content-Type": "application/json",
    }
    if method == "GET" and params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers=headers, method="GET")
    elif method == "POST" and payload is not None:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    else:
        req = urllib.request.Request(url, headers=headers, method=method)
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        body_txt = ""
        try:
            body_txt = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise _SlackError(f"HTTP {exc.code}: {exc.reason} — {body_txt[:200]}") from exc
    except Exception as exc:
        raise _SlackError(str(exc)) from exc

    try:
        body = json.loads(raw)
    except Exception as exc:
        raise _SlackError(f"JSON parse error: {exc}") from exc

    if not body.get("ok"):
        error = body.get("error", "(no error field)")
        raise _SlackError(f"Slack API error: {error}")

    return body


# ── Message chunking ───────────────────────────────────────────────────────────


def _chunk_text(text: str, max_chars: int = _MAX_MESSAGE_CHARS) -> list[str]:
    """Split *text* into chunks ≤ *max_chars*, preferring blank-line boundaries."""
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        split_at = remaining.rfind("\n\n", 0, max_chars)
        if split_at == -1:
            split_at = remaining.rfind("\n", 0, max_chars)
        if split_at == -1:
            split_at = max_chars
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


# ── DB query helpers ───────────────────────────────────────────────────────────


def _db_status(db: sqlite3.Connection) -> dict:
    try:
        row = db.execute("SELECT MAX(version) FROM schema_version").fetchone()
        schema_version = int(row[0]) if row and row[0] is not None else 0
    except Exception:
        schema_version = 0
    try:
        row = db.execute("SELECT COUNT(*) FROM sessions").fetchone()
        session_count = int(row[0]) if row else 0
    except Exception:
        session_count = 0
    try:
        row = db.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()
        knowledge_count = int(row[0]) if row else 0
    except Exception:
        knowledge_count = 0
    return {
        "schema_version": schema_version,
        "sessions": session_count,
        "knowledge_entries": knowledge_count,
    }


def _db_search(db: sqlite3.Connection, query: str, limit: int = _SEARCH_LIMIT) -> list[dict]:
    from browse.core.fts import _probe_sessions_fts, _sanitize_fts_query

    if not _probe_sessions_fts(db):
        return []
    safe_q = _sanitize_fts_query(query)
    try:
        rows = list(
            db.execute(
                """
                SELECT s.id, s.summary, s.source
                FROM sessions s
                WHERE s.id IN (
                    SELECT session_id FROM sessions_fts WHERE sessions_fts MATCH ?
                )
                LIMIT ?
                """,
                (safe_q, limit),
            )
        )
    except sqlite3.OperationalError:
        return []
    return [
        {
            "id": r["id"] if hasattr(r, "__getitem__") else r[0],
            "summary": (r["summary"] if hasattr(r, "__getitem__") else r[1]) or "(no summary)",
            "source": (r["source"] if hasattr(r, "__getitem__") else r[2]) or "",
        }
        for r in rows
    ]


def _db_recent(db: sqlite3.Connection, limit: int = _RECENT_LIMIT) -> list[dict]:
    try:
        rows = list(
            db.execute(
                "SELECT id, summary, source FROM sessions ORDER BY rowid DESC LIMIT ?",
                (limit,),
            )
        )
    except Exception:
        return []
    return [
        {
            "id": r["id"] if hasattr(r, "__getitem__") else r[0],
            "summary": (r["summary"] if hasattr(r, "__getitem__") else r[1]) or "(no summary)",
            "source": (r["source"] if hasattr(r, "__getitem__") else r[2]) or "",
        }
        for r in rows
    ]


def _run_briefing(topic: str) -> str:
    tools_dir = Path(__file__).resolve().parent.parent.parent
    briefing_script = tools_dir / "briefing.py"
    if not briefing_script.exists():
        return "briefing.py not found"
    try:
        result = subprocess.run(
            [sys.executable, str(briefing_script), topic, "--compact"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(tools_dir),
        )
        output = result.stdout.strip()
        if result.returncode != 0 and not output:
            output = result.stderr.strip() or "briefing returned no output"
    except subprocess.TimeoutExpired:
        output = "briefing timed out after 30 s"
    except Exception as exc:
        output = f"briefing error: {exc}"
    if len(output) > 39000:
        output = output[:39000] + "\n…(truncated)"
    return output or "(empty briefing)"


# ── Command dispatcher ─────────────────────────────────────────────────────────


class _CommandDispatcher:
    def __init__(self, db: sqlite3.Connection, start_time: float) -> None:
        self._db = db
        self._start_time = start_time

    def dispatch(self, text: str) -> str:
        text = (text or "").strip()
        if not text.startswith("/"):
            return "Send /help to see available commands."
        parts = text.split(None, 1)
        cmd_raw = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        try:
            if cmd_raw == "/status":
                return self._cmd_status()
            elif cmd_raw == "/search":
                return self._cmd_search(arg)
            elif cmd_raw == "/briefing":
                return self._cmd_briefing(arg)
            elif cmd_raw == "/recent":
                return self._cmd_recent()
            elif cmd_raw in ("/help", "/start"):
                return _HELP_TEXT
            else:
                return f"Unknown command: {cmd_raw}\nSend /help to see available commands."
        except Exception as exc:
            return f"Error processing {cmd_raw}: {exc}"

    def _cmd_status(self) -> str:
        uptime_s = int(time.monotonic() - self._start_time)
        hours, rem = divmod(uptime_s, 3600)
        mins, secs = divmod(rem, 60)
        info = _db_status(self._db)
        return (
            "*Hindsight Browse — status*\n\n"
            f"Uptime: {hours}h {mins}m {secs}s\n"
            f"Sessions indexed: {info['sessions']}\n"
            f"Knowledge entries: {info['knowledge_entries']}\n"
            f"DB schema version: {info['schema_version']}\n"
            f"Mode: broker/slack (HTTP-polling, read-only, no inbound port)"
        )

    def _cmd_search(self, query: str) -> str:
        if not query:
            return "Usage: /search <query>\nExample: /search authentication"
        results = _db_search(self._db, query)
        if not results:
            return f"No results for: {query}"
        lines = [f"*Search: {query}* — {len(results)} result(s)\n"]
        for i, r in enumerate(results, 1):
            summary = r["summary"][:120].replace("*", "").replace("_", "")
            lines.append(f"{i}. `{r['id']}`\n   {summary}")
        return "\n".join(lines)

    def _cmd_briefing(self, topic: str) -> str:
        if not topic:
            return "Usage: /briefing <topic>\nExample: /briefing authentication patterns"
        return _run_briefing(topic)

    def _cmd_recent(self) -> str:
        rows = _db_recent(self._db)
        if not rows:
            return "No sessions found."
        lines = [f"*Recent {len(rows)} sessions:*\n"]
        for i, r in enumerate(rows, 1):
            summary = r["summary"][:100].replace("*", "").replace("_", "")
            lines.append(f"{i}. `{r['id']}`\n   {summary}")
        return "\n".join(lines)


# ── Rate-limiter ───────────────────────────────────────────────────────────────


class _RateLimiter:
    def __init__(self, min_interval: float = _MIN_SEND_INTERVAL) -> None:
        self._min_interval = min_interval
        self._last_send: float = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_send
            remaining = self._min_interval - elapsed
            if remaining > 0:
                time.sleep(remaining)
            self._last_send = time.monotonic()


# ── Main broker class ──────────────────────────────────────────────────────────


class SlackBroker:
    """Slack HTTP-polling broker for browse.py (issue #72).

    Uses the Slack Web API to poll a channel for messages and post responses.
    No WebSocket, no inbound port. Pure stdlib.

    Remaining blocker for #72 full closure: live credentials + maintainer RTT
    benchmark (median + p95 over ≥100 messages on a FPT network).

    Parameters
    ----------
    db:
        Open sqlite3 connection to knowledge.db.
    bot_token:
        Slack bot OAuth token (xoxb-..., from BROWSE_BROKER_SLACK_BOT_TOKEN).
    channel_id:
        Slack channel ID (e.g. 'C01ABCDEF', from BROWSE_BROKER_SLACK_CHANNEL_ID).
    authorized_user_id:
        Slack user ID to whitelist (from BROWSE_BROKER_SLACK_AUTHORIZED_USER_ID).
        All other user IDs are silently dropped.
    poll_interval:
        Seconds between conversations.history polls (default 2.0).
    """

    def __init__(
        self,
        db: sqlite3.Connection,
        bot_token: str,
        channel_id: str,
        authorized_user_id: str,
        poll_interval: float = _POLL_INTERVAL,
    ) -> None:
        self._db = db
        self._bot_token = bot_token
        self._channel_id = channel_id
        self._authorized_uid = authorized_user_id
        self._poll_interval = poll_interval
        self._opener = _no_proxy_opener()
        self._limiter = _RateLimiter()
        self._start_time = time.monotonic()
        self._dispatcher = _CommandDispatcher(db=db, start_time=self._start_time)

    # ── Public API ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Start the HTTP-polling loop.  Blocks until KeyboardInterrupt or fatal error."""
        print(
            "[broker/slack] Starting HTTP-polling loop (no inbound port opened; ~2 s poll interval). Ctrl-C to stop.",
            flush=True,
        )
        self._verify_bot_identity()
        # Use a small offset from now so we don't replay existing messages
        last_ts = f"{time.time() - 0.5:.6f}"
        consecutive_errors = 0

        while True:
            try:
                time.sleep(self._poll_interval)
                messages, last_ts = self._poll_messages(oldest=last_ts)
                consecutive_errors = 0
            except KeyboardInterrupt:
                print("\n[broker/slack] Stopping.", flush=True)
                return
            except _SlackError as exc:
                consecutive_errors += 1
                wait = min(2**consecutive_errors, 60)
                print(
                    f"[broker/slack] Poll error ({exc}); retrying in {wait}s",
                    file=sys.stderr,
                    flush=True,
                )
                try:
                    time.sleep(wait)
                except KeyboardInterrupt:
                    print("\n[broker/slack] Stopping.", flush=True)
                    return
                continue

            # conversations.history returns newest-first; reverse for chronological
            for msg in reversed(messages):
                self._handle_message(msg)

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _verify_bot_identity(self) -> None:
        """Call auth.test to verify the bot token. Exits on failure."""
        try:
            result = _slack_request(self._opener, self._bot_token, "GET", "auth.test")
            user = result.get("user", "?")
            bot_id = result.get("bot_id", result.get("user_id", "?"))
            print(
                f"[broker/slack] Authenticated as @{user} (id={bot_id})",
                flush=True,
            )
        except _SlackError as exc:
            print(
                f"[broker/slack] FATAL: auth.test failed — {exc}\nCheck BROWSE_BROKER_SLACK_BOT_TOKEN.",
                file=sys.stderr,
                flush=True,
            )
            sys.exit(1)

    def _poll_messages(self, oldest: str) -> tuple[list[dict], str]:
        """Fetch messages from the channel posted after *oldest* (Slack timestamp string).

        Returns (messages_list, new_oldest_cursor).
        conversations.history returns newest-first; we reverse in run().
        """
        result = _slack_request(
            self._opener,
            self._bot_token,
            "GET",
            "conversations.history",
            params={
                "channel": self._channel_id,
                "oldest": oldest,
                "inclusive": "false",
                "limit": "100",
            },
        )
        messages = result.get("messages", [])
        # Update oldest cursor to the newest message timestamp seen
        new_oldest = oldest
        for msg in messages:
            ts = msg.get("ts", "")
            if ts and ts > new_oldest:
                new_oldest = ts
        return messages, new_oldest

    def _handle_message(self, message: dict) -> None:
        """Process one Slack message; silently drop unauthorized users and bot messages."""
        # Drop bot messages (prevent loops with our own replies)
        if message.get("subtype") == "bot_message" or message.get("bot_id"):
            return
        user_id = str(message.get("user") or "")
        if user_id != self._authorized_uid:
            return
        text = (message.get("text") or "").strip()
        if not text:
            return
        response_text = self._dispatcher.dispatch(text)
        self._send_chunks(response_text)

    def _send_chunks(self, text: str) -> None:
        for chunk in _chunk_text(text):
            self._limiter.wait()
            self._post_message(chunk)

    def _post_message(self, text: str) -> None:
        """Post a single message to the channel. Logs errors; does not raise."""
        payload = {
            "channel": self._channel_id,
            "text": text,
            "mrkdwn": True,
        }
        try:
            _slack_request(
                self._opener,
                self._bot_token,
                "POST",
                "chat.postMessage",
                payload=payload,
            )
        except _SlackError as exc:
            print(
                f"[broker/slack] postMessage error: {exc}",
                file=sys.stderr,
                flush=True,
            )
