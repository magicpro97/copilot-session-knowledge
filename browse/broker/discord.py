"""browse/broker/discord.py — Discord HTTP-polling broker for browse.py (issue #72).

Architecture (§5 Outbound Control-Bus Mode):
  - Polls discord.com REST API channel message history using stdlib urllib
    (NO WebSocket; NO inbound port opened).
  - Dispatches /status, /search, /briefing, /recent, /help commands posted to
    the configured Discord channel by the authorized user.
  - Auth: silently drops every message from any user_id != authorized_user_id.
  - Rate limiting: enforces Discord's per-channel rate limits (~2 sends/s safe).
  - Chunks replies >2000 chars at blank-line / newline boundaries (Discord limit).
  - Proxy bypass: uses ProxyHandler({}) so tunnel-hostile proxy env vars do NOT
    intercept outbound Discord API calls.

Architecture constraint (IMPORTANT):
  This broker uses HTTP polling of the channel messages history endpoint
  (GET /channels/{channel_id}/messages?after={last_snowflake}) rather than the
  Discord Gateway WebSocket API.  This is stdlib-only (no pip dependencies) but
  introduces ~2 s poll latency versus <100 ms for the Gateway.

  If sub-second latency is required the Discord Gateway (WebSocket) must be used,
  which requires a non-stdlib dependency (e.g. discord.py ≥2.0 or websockets≥10).

Remaining blockers for #72 full closure:
  1. credentials — BROWSE_BROKER_DISCORD_TOKEN,
                   BROWSE_BROKER_DISCORD_CHANNEL_ID,
                   BROWSE_BROKER_DISCORD_AUTHORIZED_USER_ID
  2. Maintainer must measure median + p95 RTT over ≥100 messages with real
     credentials on a tunnel-hostile (FPT) network.
  3. FPT-simulation run with real credentials has not been performed.

Entry point:
  broker = DiscordBroker(db=db, token=token, channel_id=channel_id,
                          authorized_user_id=uid)
  broker.run()  # blocks; raises KeyboardInterrupt on Ctrl-C

Environment (read by browse/__init__.py, passed here):
  BROWSE_BROKER_DISCORD_TOKEN              — Discord bot token (required)
  BROWSE_BROKER_DISCORD_CHANNEL_ID         — channel ID to poll / respond in (required)
  BROWSE_BROKER_DISCORD_AUTHORIZED_USER_ID — single string Discord user ID (required)

Stdlib-only: no third-party packages. No WebSocket. No inbound port.
"""

import base64
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

_DISCORD_API_BASE = "https://discord.com/api/v10"
# Discord snowflake epoch: 2015-01-01T00:00:00.000 UTC in milliseconds.
# Snowflakes are (unix_ms - _DISCORD_EPOCH_MS) << 22 | (worker << 12) | seq.
# Only the top 42 bits (timestamp) matter for a startup lower-bound cursor.
_DISCORD_EPOCH_MS = 1420070400000
_POLL_INTERVAL = 2.0  # seconds between message-history polls
_MIN_SEND_INTERVAL = 0.55  # ~2 msg/s; Discord allows ~5/5 s per channel
_MAX_MESSAGE_CHARS = 2000  # Discord hard message length limit
_SEARCH_LIMIT = 5
_RECENT_LIMIT = 10
_HELP_TEXT = f"""\
**Hindsight Browse — available commands**

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


def _make_now_snowflake() -> str:
    """Return a synthetic Discord snowflake whose timestamp equals the current wall-clock time.

    Discord snowflakes are 64-bit integers with the top 42 bits encoding
    milliseconds since _DISCORD_EPOCH_MS.  Any real Discord message posted
    *before* this call will have a strictly smaller snowflake ID.

    Using this as the ``after=`` cursor on startup guarantees that
    _poll_messages() can only return messages posted *after* the broker
    started — even when both live cursor probes fail.  The worker/sequence
    bits are left as zero; Discord compares only by snowflake magnitude.
    """
    now_ms = int(time.time() * 1000)
    return str((now_ms - _DISCORD_EPOCH_MS) << 22)


# ── Discord API helpers ────────────────────────────────────────────────────────


class _DiscordError(Exception):
    """Wraps non-200 / parse errors from the Discord API."""


def _discord_request(
    opener: urllib.request.OpenerDirector,
    token: str,
    method: str,
    path: str,
    payload: dict | None = None,
    timeout: int = 15,
) -> object:
    """Send a Discord REST API request; return the parsed JSON body.

    method  — 'GET' or 'POST'
    path    — relative path, e.g. '/channels/123/messages'
    Raises _DiscordError on HTTP errors or non-2xx responses.
    """
    url = f"{_DISCORD_API_BASE}{path}"
    headers = {
        "Authorization": f"Bot {token}",
        "User-Agent": "HindsightBrowseBroker/1 (stdlib)",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
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
        raise _DiscordError(f"HTTP {exc.code}: {exc.reason} — {body_txt[:200]}") from exc
    except Exception as exc:
        raise _DiscordError(str(exc)) from exc

    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception as exc:
        raise _DiscordError(f"JSON parse error: {exc}") from exc


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
    if len(output) > 1800:
        output = output[:1800] + "\n…(truncated)"
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
            "**Hindsight Browse — status**\n\n"
            f"Uptime: {hours}h {mins}m {secs}s\n"
            f"Sessions indexed: {info['sessions']}\n"
            f"Knowledge entries: {info['knowledge_entries']}\n"
            f"DB schema version: {info['schema_version']}\n"
            f"Mode: broker/discord (HTTP-polling, read-only, no inbound port)"
        )

    def _cmd_search(self, query: str) -> str:
        if not query:
            return "Usage: /search <query>\nExample: /search authentication"
        results = _db_search(self._db, query)
        if not results:
            return f"No results for: {query}"
        lines = [f"**Search: {query}** — {len(results)} result(s)\n"]
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
        lines = [f"**Recent {len(rows)} sessions:**\n"]
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


class DiscordBroker:
    """Discord HTTP-polling broker for browse.py (issue #72).

    Uses the Discord REST API to poll a channel for commands and post responses.
    No WebSocket, no inbound port. Pure stdlib.

    Remaining blocker for #72 full closure: live credentials + maintainer RTT
    benchmark (median + p95 over ≥100 messages on a FPT network).

    Parameters
    ----------
    db:
        Open sqlite3 connection to knowledge.db.
    token:
        Discord bot token (from BROWSE_BROKER_DISCORD_TOKEN).
    channel_id:
        Discord channel ID (string snowflake) to poll and respond in.
    authorized_user_id:
        Discord user ID (string) to whitelist.  All other users are silently
        dropped — no error reply is sent.
    poll_interval:
        Seconds between channel history polls (default 2.0).
    """

    def __init__(
        self,
        db: sqlite3.Connection,
        token: str,
        channel_id: str,
        authorized_user_id: str,
        poll_interval: float = _POLL_INTERVAL,
    ) -> None:
        self._db = db
        self._token = token
        self._channel_id = str(channel_id)
        self._authorized_uid = str(authorized_user_id)
        self._poll_interval = poll_interval
        self._opener = _no_proxy_opener()
        self._limiter = _RateLimiter()
        self._start_time = time.monotonic()
        self._dispatcher = _CommandDispatcher(db=db, start_time=self._start_time)

    # ── Public API ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Start the HTTP-polling loop.  Blocks until KeyboardInterrupt or fatal error."""
        print(
            "[broker/discord] Starting HTTP-polling loop (no inbound port opened; ~2 s poll interval). Ctrl-C to stop.",
            flush=True,
        )
        self._verify_bot_identity()
        last_id = self._get_latest_message_id()
        # Guard: if initial cursor fetch failed (returned "0"), advance past the
        # current backlog silently so we do not dispatch historical messages on
        # the first real poll.
        if last_id == "0":
            last_id = self._catch_up_cursor()
        consecutive_errors = 0

        while True:
            try:
                time.sleep(self._poll_interval)
                messages = self._poll_messages(after=last_id)
                consecutive_errors = 0
            except KeyboardInterrupt:
                print("\n[broker/discord] Stopping.", flush=True)
                return
            except _DiscordError as exc:
                consecutive_errors += 1
                wait = min(2**consecutive_errors, 60)
                print(
                    f"[broker/discord] Poll error ({exc}); retrying in {wait}s",
                    file=sys.stderr,
                    flush=True,
                )
                try:
                    time.sleep(wait)
                except KeyboardInterrupt:
                    print("\n[broker/discord] Stopping.", flush=True)
                    return
                continue

            # Discord returns messages newest-first; reverse to chronological order
            for msg in reversed(messages):
                msg_id = msg.get("id", "")
                if msg_id > last_id:
                    last_id = msg_id
                self._handle_message(msg)

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _verify_bot_identity(self) -> None:
        """Call GET /users/@me to confirm the token is valid. Exits on failure."""
        try:
            me = _discord_request(self._opener, self._token, "GET", "/users/@me")
            username = me.get("username", "?") if isinstance(me, dict) else "?"
            uid = me.get("id", "?") if isinstance(me, dict) else "?"
            print(
                f"[broker/discord] Authenticated as {username}#{me.get('discriminator', '0')} (id={uid})",
                flush=True,
            )
        except _DiscordError as exc:
            print(
                f"[broker/discord] FATAL: GET /users/@me failed — {exc}\nCheck BROWSE_BROKER_DISCORD_TOKEN.",
                file=sys.stderr,
                flush=True,
            )
            sys.exit(1)

    def _get_latest_message_id(self) -> str:
        """Return the ID of the most recent message in the channel (or '0')."""
        try:
            msgs = _discord_request(
                self._opener,
                self._token,
                "GET",
                f"/channels/{self._channel_id}/messages?limit=1",
            )
            if isinstance(msgs, list) and msgs:
                return msgs[0].get("id", "0")
        except _DiscordError as exc:
            print(
                f"[broker/discord] Warning: could not get initial message ID — {exc}",
                file=sys.stderr,
                flush=True,
            )
        return "0"

    def _catch_up_cursor(self) -> str:
        """Advance the startup cursor past the current backlog without dispatching.

        Called from run() when _get_latest_message_id() failed (returned '0').
        Fetches the most-recent messages once and returns the highest snowflake
        ID seen, so the first real poll only delivers genuinely new messages.

        On fetch failure the replay guarantee still holds: instead of returning
        '0' (which would cause _poll_messages to omit the lower bound and fetch
        up to 100 historical messages), this method returns a synthetic snowflake
        for the current wall-clock time.  Any real Discord message posted before
        this moment has a strictly smaller snowflake, so the first successful
        poll can only return messages posted *after* startup.
        """
        try:
            messages = self._poll_messages(after="0")
        except _DiscordError as exc:
            now_snowflake = _make_now_snowflake()
            print(
                f"[broker/discord] Warning: startup catch-up fetch failed — {exc}; "
                f"using synthetic cursor {now_snowflake} to prevent backlog replay.",
                file=sys.stderr,
                flush=True,
            )
            return now_snowflake
        ids = [m.get("id", "") for m in messages if m.get("id")]
        if ids:
            return max(ids)
        return "0"

    def _poll_messages(self, after: str = "0") -> list[dict]:
        """Return messages in the channel posted after *after* (snowflake ID)."""
        path = f"/channels/{self._channel_id}/messages?limit=100"
        if after and after != "0":
            path += f"&after={after}"
        result = _discord_request(self._opener, self._token, "GET", path)
        return result if isinstance(result, list) else []

    def _handle_message(self, message: dict) -> None:
        """Process one Discord message; silently drop unauthorized users."""
        author = message.get("author") or {}
        author_id = str(author.get("id", ""))
        if author_id != self._authorized_uid:
            return
        # Ignore bot messages (prevent loops)
        if author.get("bot"):
            return
        content = (message.get("content") or "").strip()
        if not content:
            return
        response_text = self._dispatcher.dispatch(content)
        self._send_chunks(response_text)

    def _send_chunks(self, text: str) -> None:
        """Split *text* if necessary and send each chunk respecting rate limits."""
        for chunk in _chunk_text(text):
            self._limiter.wait()
            self._send_message(chunk)

    def _send_message(self, content: str) -> None:
        """POST a single message to the channel.  Logs errors; does not raise."""
        payload = {"content": content}
        try:
            _discord_request(
                self._opener,
                self._token,
                "POST",
                f"/channels/{self._channel_id}/messages",
                payload=payload,
            )
        except _DiscordError as exc:
            print(
                f"[broker/discord] sendMessage error: {exc}",
                file=sys.stderr,
                flush=True,
            )
