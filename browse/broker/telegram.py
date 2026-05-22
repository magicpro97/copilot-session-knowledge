"""browse/broker/telegram.py — Outbound-only Telegram broker for browse.py (issue #71).

Architecture (§5 Outbound Control-Bus Mode):
  - Long-polls api.telegram.org using stdlib urllib (NO inbound port opened).
  - Dispatches /status, /search, /briefing, /recent, /help commands.
  - Auth: silently drops every update from any user_id != authorized_user_id.
  - Rate limiting: enforces ~1 message/s per Telegram recommendation.
  - Chunks replies >4096 chars at blank-line / newline boundaries.
  - Proxy bypass: uses ProxyHandler({}) so tunnel-hostile proxy env vars do NOT
    intercept outbound Telegram API calls (mirrors _probe_public_url pattern).

Entry point:
  broker = TelegramBroker(db=db, token=tg_token, authorized_user_id=uid)
  broker.run()  # blocks; raises KeyboardInterrupt on Ctrl-C

Environment (read by browse/__init__.py, passed here):
  BROWSE_BROKER_TELEGRAM_TOKEN       — Telegram bot token (required)
  BROWSE_BROKER_AUTHORIZED_USER_ID   — single integer Telegram user ID (required)

Stdlib-only: no third-party packages.
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

_TG_API_BASE = "https://api.telegram.org/bot{token}"
_POLL_TIMEOUT = 25  # long-poll timeout (seconds) — <30 keeps conn alive
_MIN_SEND_INTERVAL = 1.05  # Telegram ~1 msg/s/chat limit + small margin
_MAX_MESSAGE_CHARS = 4096  # Telegram hard message length limit
_SEARCH_LIMIT = 5  # results returned by /search
_RECENT_LIMIT = 10  # sessions returned by /recent
_HELP_TEXT = f"""\
*Hindsight Browse — available commands*

/status — daemon status (uptime, session count, DB version)
/search <query> — search the knowledge base (top {_SEARCH_LIMIT} results)
/briefing <topic> — run briefing for a topic
/recent — list the {_RECENT_LIMIT} most recent sessions
/help — this message

_All commands are read-only. Auth: single-user whitelist._
"""


# ── Proxy-bypass opener (mirrors browse/__init__.py:_probe_public_url) ────────


def _no_proxy_opener() -> urllib.request.OpenerDirector:
    """Return a urllib opener that bypasses HTTP(S)_PROXY environment variables.

    On tunnel-hostile networks operators may have no proxy set; on corporate
    networks a proxy may be configured.  Either way, outbound Telegram API
    calls must use direct HTTPS, not any local proxy that may block or MITM
    the connection.
    """
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ── Telegram API helpers ──────────────────────────────────────────────────────


class _TelegramError(Exception):
    """Wraps non-200 / parse errors from the Telegram API."""


def _tg_request(
    opener: urllib.request.OpenerDirector,
    base_url: str,
    method: str,
    payload: dict | None = None,
    timeout: int = 30,
) -> dict:
    """POST or GET a Telegram Bot API method; return the result dict.

    Raises _TelegramError on HTTP errors or if ok==False.
    """
    url = f"{base_url}/{method}"
    if payload:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
        )
    else:
        req = urllib.request.Request(url)
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise _TelegramError(f"HTTP {exc.code}: {exc.reason}") from exc
    except Exception as exc:
        raise _TelegramError(str(exc)) from exc

    try:
        body = json.loads(raw)
    except Exception as exc:
        raise _TelegramError(f"JSON parse error: {exc}") from exc

    if not body.get("ok"):
        desc = body.get("description", "(no description)")
        raise _TelegramError(f"Telegram API error: {desc}")

    return body.get("result")


# ── Message chunking ──────────────────────────────────────────────────────────


def _chunk_text(text: str, max_chars: int = _MAX_MESSAGE_CHARS) -> list[str]:
    """Split *text* into chunks ≤ *max_chars*, preferring blank-line boundaries.

    Falls back to newline splits, then hard character splits.
    """
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        # Prefer splitting at a blank line (paragraph boundary)
        split_at = remaining.rfind("\n\n", 0, max_chars)
        if split_at == -1:
            # Fall back to any newline
            split_at = remaining.rfind("\n", 0, max_chars)
        if split_at == -1:
            # Hard split
            split_at = max_chars
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


# ── DB query helpers ──────────────────────────────────────────────────────────


def _db_status(db: sqlite3.Connection) -> dict:
    """Return a lightweight status dict from the DB."""
    try:
        schema_row = db.execute("SELECT MAX(version) FROM schema_version").fetchone()
        schema_version = int(schema_row[0]) if schema_row and schema_row[0] is not None else 0
    except Exception:
        schema_version = 0
    try:
        sessions_row = db.execute("SELECT COUNT(*) FROM sessions").fetchone()
        session_count = int(sessions_row[0]) if sessions_row else 0
    except Exception:
        session_count = 0
    try:
        ke_row = db.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()
        knowledge_count = int(ke_row[0]) if ke_row else 0
    except Exception:
        knowledge_count = 0
    return {
        "schema_version": schema_version,
        "sessions": session_count,
        "knowledge_entries": knowledge_count,
    }


def _db_search(db: sqlite3.Connection, query: str, limit: int = _SEARCH_LIMIT) -> list[dict]:
    """Run FTS5 search across sessions; return list of result dicts."""
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

    results = []
    for row in rows:
        results.append(
            {
                "id": row["id"] if hasattr(row, "__getitem__") else row[0],
                "summary": (row["summary"] if hasattr(row, "__getitem__") else row[1]) or "(no summary)",
                "source": (row["source"] if hasattr(row, "__getitem__") else row[2]) or "",
            }
        )
    return results


def _db_recent(db: sqlite3.Connection, limit: int = _RECENT_LIMIT) -> list[dict]:
    """Return the most recent sessions ordered by rowid desc."""
    try:
        rows = list(
            db.execute(
                """
                SELECT id, summary, source
                FROM sessions
                ORDER BY rowid DESC
                LIMIT ?
                """,
                (limit,),
            )
        )
    except Exception:
        return []
    results = []
    for row in rows:
        results.append(
            {
                "id": row["id"] if hasattr(row, "__getitem__") else row[0],
                "summary": (row["summary"] if hasattr(row, "__getitem__") else row[1]) or "(no summary)",
                "source": (row["source"] if hasattr(row, "__getitem__") else row[2]) or "",
            }
        )
    return results


def _run_briefing(topic: str) -> str:
    """Run briefing.py as a subprocess and return its stdout (max 4000 chars).

    Uses --compact for a short, token-efficient output suitable for Telegram.
    Times out after 30 seconds and returns a partial result or error message.
    """
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
    # Telegram hard limit; leave room for a trailing "…" marker
    if len(output) > 3900:
        output = output[:3900] + "\n…(truncated)"
    return output or "(empty briefing)"


# ── Command dispatcher ────────────────────────────────────────────────────────


class _CommandDispatcher:
    """Parses incoming Telegram message text and dispatches to handler methods."""

    def __init__(self, db: sqlite3.Connection, start_time: float) -> None:
        self._db = db
        self._start_time = start_time

    def dispatch(self, text: str) -> str:
        """Return response text for a command string.  Never raises."""
        text = (text or "").strip()
        if not text.startswith("/"):
            return "Send /help to see available commands."

        # Split into command and argument (strip optional @BotUsername suffix)
        parts = text.split(None, 1)
        cmd_raw = parts[0].lower()
        # Strip @username suffix if present (e.g. /search@MyBot)
        if "@" in cmd_raw:
            cmd_raw = cmd_raw.split("@", 1)[0]
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

    # ── Individual command handlers ───────────────────────────────────────────

    def _cmd_status(self) -> str:
        uptime_s = int(time.monotonic() - self._start_time)
        hours, rem = divmod(uptime_s, 3600)
        mins, secs = divmod(rem, 60)
        uptime_str = f"{hours}h {mins}m {secs}s"
        info = _db_status(self._db)
        return (
            "*Hindsight Browse — status*\n\n"
            f"Uptime: {uptime_str}\n"
            f"Sessions indexed: {info['sessions']}\n"
            f"Knowledge entries: {info['knowledge_entries']}\n"
            f"DB schema version: {info['schema_version']}\n"
            f"Mode: broker/telegram (read-only, outbound-only)"
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


# ── Rate-limiter ──────────────────────────────────────────────────────────────


class _RateLimiter:
    """Enforces a minimum interval between successive send_message calls."""

    def __init__(self, min_interval: float = _MIN_SEND_INTERVAL) -> None:
        self._min_interval = min_interval
        self._last_send: float = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        """Block until the minimum interval since the last send has elapsed."""
        with self._lock:
            elapsed = time.monotonic() - self._last_send
            remaining = self._min_interval - elapsed
            if remaining > 0:
                time.sleep(remaining)
            self._last_send = time.monotonic()


# ── Main broker class ─────────────────────────────────────────────────────────


class TelegramBroker:
    """Outbound-only Telegram broker for browse.py (issue #71).

    Parameters
    ----------
    db:
        Open sqlite3 connection to knowledge.db.
    token:
        Telegram bot token (from BROWSE_BROKER_TELEGRAM_TOKEN).
    authorized_user_id:
        Integer Telegram user_id to whitelist.  Every other user_id is silently
        dropped — no error reply is sent (mirrors iletai pattern).
    """

    def __init__(
        self,
        db: sqlite3.Connection,
        token: str,
        authorized_user_id: int,
    ) -> None:
        self._db = db
        self._token = token
        self._authorized_uid = int(authorized_user_id)
        self._base_url = _TG_API_BASE.format(token=token)
        self._opener = _no_proxy_opener()
        self._limiter = _RateLimiter()
        self._start_time = time.monotonic()
        self._dispatcher = _CommandDispatcher(db=db, start_time=self._start_time)

    # ── Public API ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Start the long-poll loop.  Blocks until KeyboardInterrupt or fatal error."""
        print(
            "[broker/telegram] Starting outbound-only long-poll loop (no inbound port opened). Ctrl-C to stop.",
            flush=True,
        )
        self._verify_bot_identity()
        offset = 0
        consecutive_errors = 0

        while True:
            try:
                updates = self._get_updates(offset)
                consecutive_errors = 0
            except KeyboardInterrupt:
                print("\n[broker/telegram] Stopping.", flush=True)
                return
            except _TelegramError as exc:
                consecutive_errors += 1
                wait = min(2**consecutive_errors, 60)
                print(
                    f"[broker/telegram] Poll error ({exc}); retrying in {wait}s",
                    file=sys.stderr,
                    flush=True,
                )
                try:
                    time.sleep(wait)
                except KeyboardInterrupt:
                    print("\n[broker/telegram] Stopping.", flush=True)
                    return
                continue

            for update in updates:
                offset = max(offset, update.get("update_id", 0) + 1)
                self._handle_update(update)

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _verify_bot_identity(self) -> None:
        """Call getMe to confirm the token is valid.  Exits on failure."""
        try:
            me = _tg_request(self._opener, self._base_url, "getMe")
            username = me.get("username", "?")
            uid = me.get("id", "?")
            print(
                f"[broker/telegram] Authenticated as @{username} (id={uid})",
                flush=True,
            )
        except _TelegramError as exc:
            print(
                f"[broker/telegram] FATAL: getMe failed — {exc}\nCheck BROWSE_BROKER_TELEGRAM_TOKEN.",
                file=sys.stderr,
                flush=True,
            )
            sys.exit(1)

    def _get_updates(self, offset: int) -> list[dict]:
        """Perform one long-poll call.  Returns a (possibly empty) list of updates."""
        payload = {
            "offset": offset,
            "timeout": _POLL_TIMEOUT,
            "allowed_updates": ["message"],
        }
        result = _tg_request(
            self._opener,
            self._base_url,
            "getUpdates",
            payload=payload,
            timeout=_POLL_TIMEOUT + 10,
        )
        return result if isinstance(result, list) else []

    def _handle_update(self, update: dict) -> None:
        """Dispatch one Telegram update; silently drop unauthorised users."""
        message = update.get("message")
        if not message:
            return

        sender = message.get("from") or {}
        sender_id = sender.get("id")

        # Auth: silent-drop any non-authorised user
        if sender_id != self._authorized_uid:
            return

        chat_id = message.get("chat", {}).get("id")
        text = message.get("text", "")
        if not chat_id or not text:
            return

        response_text = self._dispatcher.dispatch(text)
        self._send_chunks(chat_id, response_text)

    def _send_chunks(self, chat_id: int, text: str) -> None:
        """Split *text* if necessary and send each chunk respecting rate limits."""
        chunks = _chunk_text(text)
        for chunk in chunks:
            self._limiter.wait()
            self._send_message(chat_id, chunk)

    def _send_message(self, chat_id: int, text: str) -> None:
        """Send a single Telegram message.  Logs errors; does not raise."""
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }
        try:
            _tg_request(self._opener, self._base_url, "sendMessage", payload=payload)
        except _TelegramError as exc:
            print(
                f"[broker/telegram] sendMessage error: {exc}",
                file=sys.stderr,
                flush=True,
            )
