"""browse/broker/ably.py — Ably REST-polling broker for browse.py (issue #72).

Architecture (§5 Outbound Control-Bus Mode):
  - Polls Ably REST API channel message history using stdlib urllib
    (NO WebSocket; NO inbound port opened).
  - Dispatches /status, /search, /briefing, /recent, /help commands published
    to the inbound channel by the authorized client.
  - Auth: silently drops every message from a clientId != authorized_client_id.
    IMPORTANT: clientId is self-reported by the message publisher and is NOT
    server-verified by Ably unless the API key is configured with a matching
    clientId capability restriction.  See _handle_message and OPERATOR-PLAYBOOK.md
    for the security boundary details.
  - Rate limiting: enforces Ably publish limits (~50 msg/s on basic plan).
  - Chunks replies at blank-line / newline boundaries (Ably 65 KB message limit).
  - Proxy bypass: uses ProxyHandler({}) so tunnel-hostile proxy env vars do NOT
    intercept outbound Ably REST API calls.

Architecture constraint (IMPORTANT):
  This broker uses HTTP polling of the Ably REST channels history endpoint
  (GET /channels/{channel}/messages?start={ts_ms}&direction=forwards) rather
  than the Ably Realtime WebSocket API.  This is stdlib-compatible and requires
  no third-party packages, but introduces ~2 s poll latency versus the
  sub-100 ms latency of the native Ably Realtime client.

  For sub-second latency the Ably Realtime client (WebSocket) requires the
  'ably' PyPI package (non-stdlib).

Pagination limitation (KNOWN GAP):
  _poll_messages() fetches up to 100 messages per poll cycle and advances the
  cursor by max(timestamp).  If a burst produces >100 messages within a single
  2 s poll window, messages beyond the 100-item page boundary will be missed.
  Ably's REST history pagination (Link headers) is NOT followed because the
  underlying _ably_request() returns only the parsed JSON body, not response
  headers.  A full pagination fix requires a response-model refactor; the
  current implementation logs a warning when a saturated page is detected.

Remaining blockers for #72 full closure:
  1. credentials — BROWSE_BROKER_ABLY_API_KEY (format: app_id.key_id:key_secret),
                   optionally BROWSE_BROKER_ABLY_CHANNEL_IN,
                   BROWSE_BROKER_ABLY_CHANNEL_OUT,
                   BROWSE_BROKER_ABLY_AUTHORIZED_CLIENT_ID
  2. Maintainer must measure median + p95 RTT over ≥100 messages with real
     credentials on a tunnel-hostile (FPT) network.
  3. FPT-simulation run with real credentials has not been performed.
  4. clientId trust boundary: the single-clientId whitelist is only effective
     if the Ably API key's capabilities restrict the authorized clientId.
     Without that restriction any publisher with the key can spoof the whitelist.
     Operator must configure Ably key capabilities accordingly (see OPERATOR-PLAYBOOK.md).

Entry point:
  broker = AblyBroker(db=db, api_key=key, channel_in='browse-commands',
                       channel_out='browse-responses',
                       authorized_client_id='operator')
  broker.run()  # blocks; raises KeyboardInterrupt on Ctrl-C

Environment (read by browse/__init__.py, passed here):
  BROWSE_BROKER_ABLY_API_KEY              — Ably API key (required)
  BROWSE_BROKER_ABLY_CHANNEL_IN           — inbound command channel (default: browse-commands)
  BROWSE_BROKER_ABLY_CHANNEL_OUT          — outbound response channel (default: browse-responses)
  BROWSE_BROKER_ABLY_AUTHORIZED_CLIENT_ID — authorized clientId (default: operator)

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

_ABLY_REST_BASE = "https://rest.ably.io"
_POLL_INTERVAL = 2.0  # seconds between history polls
_MIN_SEND_INTERVAL = 0.02  # ~50 msg/s (Ably basic plan limit)
_MAX_MESSAGE_CHARS = 65000  # Ably message size limit (64 KB minus margin)
_SEARCH_LIMIT = 5
_RECENT_LIMIT = 10
_HELP_TEXT = f"""\
**Hindsight Browse — available commands**

/status — daemon status (uptime, session count, DB version)
/search <query> — search the knowledge base (top {_SEARCH_LIMIT} results)
/briefing <topic> — run briefing for a topic
/recent — list the {_RECENT_LIMIT} most recent sessions
/help — this message

_All commands are read-only. Auth: single-client whitelist._
"""


# ── Proxy-bypass opener ────────────────────────────────────────────────────────


def _no_proxy_opener() -> urllib.request.OpenerDirector:
    """Return a urllib opener that bypasses HTTP(S)_PROXY environment variables."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ── Ably API helpers ───────────────────────────────────────────────────────────


class _AblyError(Exception):
    """Wraps non-200 / parse errors from the Ably REST API."""


def _make_auth_header(api_key: str) -> str:
    """Return an HTTP Basic Authorization header value for an Ably API key.

    Ably API key format: '{app_id}.{key_id}:{key_secret}'
    Basic auth encodes the full key as base64(key).
    """
    encoded = base64.b64encode(api_key.encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


def _ably_request(
    opener: urllib.request.OpenerDirector,
    api_key: str,
    method: str,
    path: str,
    payload: dict | None = None,
    params: dict | None = None,
    timeout: int = 15,
) -> object:
    """Send an Ably REST API request; return the parsed JSON body.

    method  — 'GET' or 'POST'
    path    — relative path, e.g. '/channels/browse-commands/messages'
    params  — optional query-string parameters (dict)
    Raises _AblyError on HTTP errors.
    """
    url = f"{_ABLY_REST_BASE}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    headers = {
        "Authorization": _make_auth_header(api_key),
        "Content-Type": "application/json",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
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
        raise _AblyError(f"HTTP {exc.code}: {exc.reason} — {body_txt[:200]}") from exc
    except Exception as exc:
        raise _AblyError(str(exc)) from exc

    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception as exc:
        raise _AblyError(f"JSON parse error: {exc}") from exc


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
            stdin=subprocess.DEVNULL,
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
    if len(output) > 60000:
        output = output[:60000] + "\n…(truncated)"
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
            f"Mode: broker/ably (HTTP REST-polling, read-only, no inbound port)"
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


class AblyBroker:
    """Ably REST-polling broker for browse.py (issue #72).

    Uses the Ably REST API to poll a command channel for messages and publish
    responses to a response channel.  No WebSocket, no inbound port. Pure stdlib.

    Remaining blocker for #72 full closure: live credentials + maintainer RTT
    benchmark (median + p95 over ≥100 messages on a FPT network).

    Parameters
    ----------
    db:
        Open sqlite3 connection to knowledge.db.
    api_key:
        Ably API key in format 'app_id.key_id:key_secret'
        (from BROWSE_BROKER_ABLY_API_KEY).
    channel_in:
        Ably channel name for inbound operator commands (default: 'browse-commands').
    channel_out:
        Ably channel name for outbound bot responses (default: 'browse-responses').
    authorized_client_id:
        Ably clientId to whitelist for commands (default: 'operator').  Messages
        published without a matching clientId are silently dropped.
    poll_interval:
        Seconds between channel history polls (default 2.0).
    """

    def __init__(
        self,
        db: sqlite3.Connection,
        api_key: str,
        channel_in: str = "browse-commands",
        channel_out: str = "browse-responses",
        authorized_client_id: str = "operator",
        poll_interval: float = _POLL_INTERVAL,
    ) -> None:
        self._db = db
        self._api_key = api_key
        self._channel_in = channel_in
        self._channel_out = channel_out
        self._authorized_client_id = authorized_client_id
        self._poll_interval = poll_interval
        self._opener = _no_proxy_opener()
        self._limiter = _RateLimiter()
        self._start_time = time.monotonic()
        self._dispatcher = _CommandDispatcher(db=db, start_time=self._start_time)

    # ── Public API ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Start the REST-polling loop.  Blocks until KeyboardInterrupt or fatal error."""
        print(
            f"[broker/ably] Starting REST-polling loop on '{self._channel_in}' "
            f"(no inbound port; ~{self._poll_interval}s interval). Ctrl-C to stop.",
            flush=True,
        )
        self._verify_connection()
        # Start polling from 500ms in the past to avoid missing messages on startup
        last_timestamp_ms = int(time.time() * 1000) - 500
        consecutive_errors = 0

        while True:
            try:
                time.sleep(self._poll_interval)
                messages, last_timestamp_ms = self._poll_messages(since_ms=last_timestamp_ms)
                consecutive_errors = 0
            except KeyboardInterrupt:
                print("\n[broker/ably] Stopping.", flush=True)
                return
            except _AblyError as exc:
                consecutive_errors += 1
                wait = min(2**consecutive_errors, 60)
                print(
                    f"[broker/ably] Poll error ({exc}); retrying in {wait}s",
                    file=sys.stderr,
                    flush=True,
                )
                try:
                    time.sleep(wait)
                except KeyboardInterrupt:
                    print("\n[broker/ably] Stopping.", flush=True)
                    return
                continue

            for msg in messages:
                self._handle_message(msg)

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _verify_connection(self) -> None:
        """Check that the API key grants access to the inbound channel. Exits on failure."""
        try:
            _ably_request(
                self._opener,
                self._api_key,
                "GET",
                f"/channels/{urllib.parse.quote(self._channel_in, safe='')}/messages",
                params={"limit": "1"},
            )
            print(
                f"[broker/ably] API key accepted; polling '{self._channel_in}'.",
                flush=True,
            )
        except _AblyError as exc:
            print(
                f"[broker/ably] FATAL: channel probe failed — {exc}\nCheck BROWSE_BROKER_ABLY_API_KEY.",
                file=sys.stderr,
                flush=True,
            )
            sys.exit(1)

    def _poll_messages(self, since_ms: int) -> tuple[list[dict], int]:
        """Fetch messages published to channel_in since *since_ms* (epoch ms).

        Returns (messages_chronological, new_last_timestamp_ms).

        Known limitation — pagination:
          Only the first page (up to 100 messages) is fetched per call.
          If more than 100 messages arrive in a single poll window, messages
          beyond the 100-item boundary are silently dropped; a warning is logged
          to stderr.  Following Ably REST pagination (Link headers) requires
          response-header access which _ably_request() does not currently expose.
          This is safe for typical interactive usage but may lose messages under
          burst conditions.
        """
        _POLL_PAGE_LIMIT = 100
        encoded_channel = urllib.parse.quote(self._channel_in, safe="")
        result = _ably_request(
            self._opener,
            self._api_key,
            "GET",
            f"/channels/{encoded_channel}/messages",
            params={
                "start": str(since_ms + 1),  # exclusive lower bound
                "direction": "forwards",
                "limit": str(_POLL_PAGE_LIMIT),
            },
        )
        messages = result if isinstance(result, list) else []
        # Warn when a full page is returned: further messages may exist that
        # won't be fetched until the cursor advances on the next poll cycle.
        if len(messages) == _POLL_PAGE_LIMIT:
            print(
                f"[broker/ably] Warning: poll returned a full page ({_POLL_PAGE_LIMIT} messages). "
                "Messages beyond the page boundary may be missed. "
                "Pagination (Link header) is not followed by this implementation.",
                file=sys.stderr,
                flush=True,
            )
        # Ably REST history returns items in timestamp order (forwards)
        # Update cursor to the timestamp of the latest message seen
        new_ts = since_ms
        for msg in messages:
            ts = msg.get("timestamp", 0)
            if isinstance(ts, (int, float)) and ts > new_ts:
                new_ts = int(ts)
        return messages, new_ts

    def _handle_message(self, message: dict) -> None:
        """Process one Ably message; silently drop unauthorized clients.

        Security boundary: the clientId check compares the message's self-reported
        clientId against authorized_client_id.  This is only a reliable security
        gate if the Ably API key's capability is restricted to publish/subscribe
        for the authorized clientId only.  Without that Ably-side restriction, any
        holder of the API key can publish a message with any clientId value,
        effectively bypassing this check.  Operators must configure Ably key
        capabilities to enforce the clientId restriction server-side.
        See OPERATOR-PLAYBOOK.md §Ably Broker Mode for the required setup steps.
        """
        client_id = str(message.get("clientId") or "")
        if client_id != self._authorized_client_id:
            return
        data = message.get("data", "")
        text = str(data).strip() if data is not None else ""
        if not text:
            return
        response_text = self._dispatcher.dispatch(text)
        self._send_chunks(response_text)

    def _send_chunks(self, text: str) -> None:
        for chunk in _chunk_text(text):
            self._limiter.wait()
            self._publish_message(chunk)

    def _publish_message(self, data: str) -> None:
        """Publish a single message to channel_out.  Logs errors; does not raise."""
        encoded_channel = urllib.parse.quote(self._channel_out, safe="")
        payload = {"name": "response", "data": data}
        try:
            _ably_request(
                self._opener,
                self._api_key,
                "POST",
                f"/channels/{encoded_channel}/messages",
                payload=payload,
            )
        except _AblyError as exc:
            print(
                f"[broker/ably] publish error: {exc}",
                file=sys.stderr,
                flush=True,
            )
