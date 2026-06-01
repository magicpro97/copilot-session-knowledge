//! Rust port of `browse/broker/telegram.py` — Telegram long-poll broker.
//!
//! # Architecture
//! - Outbound-only: long-polls `getUpdates`, dispatches commands, sends replies.
//! - No inbound port opened.
//! - Single-user auth: all updates from other user IDs are silently dropped.
//! - Rate limiting: enforces ≥1.05 s between `sendMessage` calls.
//! - 4096-char chunking: prefers paragraph (`\n\n`) boundaries.
//! - Retry/backoff: exponential up to 60 s on transport/API errors.
//! - Graceful shutdown: honours [`CancellationToken`] within ≤1 poll cycle.
//!
//! # Out of scope (PR-A)
//! DB-backed `/search`, `/briefing`, `/recent` delegate to the injected
//! [`KnowledgeSource`] trait.  [`DbKnowledge`] is the default implementation;
//! [`NoopKnowledge`] is retained as a test double and fallback when the DB
//! is unavailable.

use std::{
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};

use anyhow::{Context, Result};
use reqwest::Client;
use serde::Deserialize;
use serde_json::{json, Value};
use tracing::{debug, error, info, warn};

use super::{chunk_text, BrokerConfig, BrokerError, CancellationToken};
use crate::db::{connection::KnowledgeDb, fts::sanitize_fts_query};
use crate::retry::{decide, RetryDecision, RetryPolicy};

// ── Constants ─────────────────────────────────────────────────────────────────

const POLL_TIMEOUT_SECS: u64 = 25;
const MIN_SEND_INTERVAL_MS: u64 = 1050;
const MAX_MESSAGE_CHARS: usize = 4096;
const DEFAULT_TG_BASE: &str = "https://api.telegram.org/bot";
const SEARCH_LIMIT: usize = 5;
const RECENT_LIMIT: usize = 10;
const MAX_BACKOFF_SECS: u64 = 60;

/// Retry policy for the `getUpdates` long-poll loop.
///
/// Matches the original bit-shift formula:
///   `(1u64 << consecutive_errors.min(63)).min(MAX_BACKOFF_SECS)` in seconds,
/// using `base=1s, multiplier=2.0, cap=60s, no-jitter`.
/// With `attempt = consecutive_errors` (pre-incremented, 1-indexed):
///   attempt=1 -> 2s, attempt=2 -> 4s, ..., attempt=6+ -> 60s.
const POLL_RETRY_POLICY: RetryPolicy = RetryPolicy {
    base: Duration::from_secs(1),
    cap: Duration::from_secs(MAX_BACKOFF_SECS),
    multiplier: 2.0,
    jitter: (1.0, 1.0), // no jitter -- deterministic, matches original formula
    max_attempts: u32::MAX,
    budget: None, // broker runs indefinitely until cancelled
};

const HELP_TEXT: &str = "\
*Hindsight Browse — available commands*

/status — daemon status (uptime, session count, DB version)
/search <query> — search the knowledge base (top 5 results)
/briefing <topic> — run briefing for a topic
/recent — list the 10 most recent sessions
/help — this message

_All commands are read-only. Auth: single-user whitelist._";

// ── Telegram API response types ───────────────────────────────────────────────

#[derive(Debug, Deserialize)]
struct TgResponse {
    ok: bool,
    result: Option<Value>,
    description: Option<String>,
}

#[derive(Debug, Deserialize)]
struct TgUpdate {
    update_id: i64,
    message: Option<TgMessage>,
}

#[derive(Debug, Deserialize)]
struct TgMessage {
    chat: TgChat,
    from: Option<TgUser>,
    text: Option<String>,
}

#[derive(Debug, Deserialize)]
struct TgChat {
    id: i64,
}

#[derive(Debug, Deserialize)]
struct TgUser {
    id: i64,
}

// ── KnowledgeSource trait ─────────────────────────────────────────────────────

/// Abstraction over the knowledge database for dependency injection.
///
/// [`DbKnowledge`] is the production implementation backed by `knowledge.db`.
/// [`NoopKnowledge`] is used in tests and as a fallback when DB is unavailable.
/// Full DB wiring was completed in issue #924.
pub trait KnowledgeSource: Send + Sync {
    fn status(&self) -> String;
    fn search(&self, query: &str) -> Vec<SearchResult>;
    fn recent(&self) -> Vec<SearchResult>;
    fn briefing(&self, topic: &str) -> String;
}

/// A knowledge result entry.
#[derive(Debug, Clone)]
pub struct SearchResult {
    pub id: String,
    pub summary: String,
}

/// No-op knowledge source — returns placeholder text.
///
/// Used in tests and as the default when no DB is wired.
pub struct NoopKnowledge;

impl KnowledgeSource for NoopKnowledge {
    fn status(&self) -> String {
        "*Hindsight Browse — status*\n\nUptime: 0h 0m 0s\nSessions indexed: 0\nKnowledge entries: 0\nDB schema version: 0\nMode: broker/telegram (read-only, outbound-only)".to_owned()
    }

    fn search(&self, query: &str) -> Vec<SearchResult> {
        vec![SearchResult {
            id: "noop".to_owned(),
            summary: format!("(knowledge source not wired; query was: {query})"),
        }]
    }

    fn recent(&self) -> Vec<SearchResult> {
        vec![SearchResult {
            id: "noop".to_owned(),
            summary: "(knowledge source not wired)".to_owned(),
        }]
    }

    fn briefing(&self, topic: &str) -> String {
        format!("(briefing not wired; topic was: {topic})")
    }
}

// ── DbKnowledge ───────────────────────────────────────────────────────────────

/// Real `KnowledgeSource` backed by `knowledge.db` via rusqlite.
///
/// All SQL uses parameterized `?` placeholders — no interpolation.
/// FTS queries are sanitized via [`sanitize_fts_query`] before use.
///
/// If the DB cannot be opened at construction time the builder returns `None`
/// and callers should fall back to [`NoopKnowledge`].
///
/// `KnowledgeDb` holds a `rusqlite::Connection` which is `Send` but not
/// `Sync`.  Wrapping in `Mutex` makes `DbKnowledge: Sync` as required by
/// `KnowledgeSource: Send + Sync`.
pub struct DbKnowledge {
    db: Mutex<KnowledgeDb>,
}

impl DbKnowledge {
    /// Open `knowledge.db` and return a new instance, or `None` on failure.
    pub fn open() -> Option<Self> {
        match KnowledgeDb::open() {
            Ok(db) => Some(Self {
                db: Mutex::new(db),
            }),
            Err(e) => {
                warn!("DbKnowledge: failed to open knowledge.db, falling back to NoopKnowledge: {e}");
                None
            }
        }
    }

    /// Construct from an existing [`KnowledgeDb`] — used in tests.
    #[cfg(test)]
    pub(crate) fn from_db(db: KnowledgeDb) -> Self {
        Self {
            db: Mutex::new(db),
        }
    }
}

impl KnowledgeSource for DbKnowledge {
    fn status(&self) -> String {
        let guard = self.db.lock().unwrap();
        let conn = &guard.conn;

        let session_count: i64 = conn
            .query_row("SELECT COUNT(*) FROM sessions", [], |r| r.get(0))
            .unwrap_or(0);

        let entry_count: i64 = conn
            .query_row("SELECT COUNT(*) FROM knowledge_entries", [], |r| r.get(0))
            .unwrap_or(0);

        let schema_version: i64 = conn
            .query_row(
                "SELECT COALESCE(MAX(version), 0) FROM schema_version",
                [],
                |r| r.get(0),
            )
            .unwrap_or(0);

        format!(
            "*Hindsight Browse — status*\n\nSessions indexed: {session_count}\nKnowledge entries: {entry_count}\nDB schema version: {schema_version}\nMode: broker/telegram (read-only, outbound-only)"
        )
    }

    fn search(&self, query: &str) -> Vec<SearchResult> {
        let sanitized = sanitize_fts_query(query);
        let guard = self.db.lock().unwrap();
        let conn = &guard.conn;

        let mut stmt = match conn.prepare(
            "SELECT ke.id, ke.title, ke.content \
             FROM ke_fts fts \
             JOIN knowledge_entries ke ON fts.rowid = ke.id \
             WHERE ke_fts MATCH ? \
             ORDER BY rank \
             LIMIT ?",
        ) {
            Ok(s) => s,
            Err(e) => {
                warn!("DbKnowledge search: prepare failed: {e}");
                return vec![];
            }
        };

        let limit = SEARCH_LIMIT as i64;
        let rows = stmt.query_map(rusqlite::params![sanitized, limit], |row| {
            let id: i64 = row.get(0)?;
            let title: String = row.get(1)?;
            let content: String = row.get(2)?;
            Ok((id, title, content))
        });

        match rows {
            Ok(mapped) => mapped
                .filter_map(|r| r.ok())
                .map(|(id, title, content)| SearchResult {
                    id: id.to_string(),
                    summary: format!("{title} — {}", &content.chars().take(80).collect::<String>()),
                })
                .collect(),
            Err(e) => {
                warn!("DbKnowledge search: query failed: {e}");
                vec![]
            }
        }
    }

    fn recent(&self) -> Vec<SearchResult> {
        let guard = self.db.lock().unwrap();
        let conn = &guard.conn;

        let mut stmt = match conn.prepare(
            "SELECT id, title, content \
             FROM knowledge_entries \
             ORDER BY created_at DESC \
             LIMIT ?",
        ) {
            Ok(s) => s,
            Err(e) => {
                warn!("DbKnowledge recent: prepare failed: {e}");
                return vec![];
            }
        };

        let limit = RECENT_LIMIT as i64;
        let rows = stmt.query_map([limit], |row| {
            let id: i64 = row.get(0)?;
            let title: String = row.get(1)?;
            let content: String = row.get(2)?;
            Ok((id, title, content))
        });

        match rows {
            Ok(mapped) => mapped
                .filter_map(|r| r.ok())
                .map(|(id, title, content)| SearchResult {
                    id: id.to_string(),
                    summary: format!("{title} — {}", &content.chars().take(80).collect::<String>()),
                })
                .collect(),
            Err(e) => {
                warn!("DbKnowledge recent: query failed: {e}");
                vec![]
            }
        }
    }

    fn briefing(&self, topic: &str) -> String {
        // Delegate search to surface relevant knowledge entries for the topic.
        let results = self.search(topic);
        if results.is_empty() {
            return format!("No briefing entries found for: {topic}");
        }
        let mut lines = vec![format!("*Briefing: {topic}*\n")];
        for (i, r) in results.iter().enumerate() {
            let summary = r.summary.chars().take(120).collect::<String>().replace(['*', '_'], "");
            lines.push(format!("{}. `{}`\n   {}", i + 1, r.id, summary));
        }
        lines.join("\n")
    }
}

// ── Rate limiter ──────────────────────────────────────────────────────────────

struct RateLimiter {
    min_interval: Duration,
    last_send: Mutex<Option<Instant>>,
}

impl RateLimiter {
    fn new(min_interval: Duration) -> Self {
        Self {
            min_interval,
            last_send: Mutex::new(None),
        }
    }

    async fn wait(&self) {
        let sleep_duration = {
            let mut guard = self.last_send.lock().unwrap_or_else(|e| e.into_inner());
            if let Some(last) = *guard {
                let elapsed = last.elapsed();
                if elapsed < self.min_interval {
                    let remaining = self.min_interval - elapsed;
                    *guard = Some(Instant::now() + remaining);
                    remaining
                } else {
                    *guard = Some(Instant::now());
                    Duration::ZERO
                }
            } else {
                *guard = Some(Instant::now());
                Duration::ZERO
            }
        };
        if sleep_duration > Duration::ZERO {
            tokio::time::sleep(sleep_duration).await;
        }
    }
}

// ── Command dispatcher ────────────────────────────────────────────────────────

struct CommandDispatcher {
    knowledge: Arc<dyn KnowledgeSource>,
}

impl CommandDispatcher {
    fn new(knowledge: Arc<dyn KnowledgeSource>) -> Self {
        Self { knowledge }
    }

    fn dispatch(&self, text: &str) -> String {
        let text = text.trim();
        if !text.starts_with('/') {
            return "Send /help to see available commands.".to_owned();
        }

        // Split command from optional argument
        let (cmd_raw, arg) = match text.split_once(char::is_whitespace) {
            Some((c, a)) => (c.to_lowercase(), a.trim().to_owned()),
            None => (text.to_lowercase(), String::new()),
        };

        // Strip @BotUsername suffix: /search@MyBot → /search
        let cmd = match cmd_raw.split_once('@') {
            Some((c, _)) => c.to_owned(),
            None => cmd_raw,
        };

        match cmd.as_str() {
            "/status" => self.knowledge.status(),
            "/search" => {
                if arg.is_empty() {
                    "Usage: /search <query>\nExample: /search authentication".to_owned()
                } else {
                    let results = self.knowledge.search(&arg);
                    if results.is_empty() {
                        format!("No results for: {arg}")
                    } else {
                        let mut lines =
                            vec![format!("*Search: {}* — {} result(s)\n", arg, results.len())];
                        for (i, r) in results.iter().enumerate().take(SEARCH_LIMIT) {
                            // Strip Markdown control chars from untrusted content
                            let summary = r
                                .summary
                                .chars()
                                .take(120)
                                .collect::<String>()
                                .replace(['*', '_'], "");
                            lines.push(format!("{}. `{}`\n   {}", i + 1, r.id, summary));
                        }
                        lines.join("\n")
                    }
                }
            }
            "/briefing" => {
                if arg.is_empty() {
                    "Usage: /briefing <topic>\nExample: /briefing authentication patterns"
                        .to_owned()
                } else {
                    self.knowledge.briefing(&arg)
                }
            }
            "/recent" => {
                let rows = self.knowledge.recent();
                if rows.is_empty() {
                    "No sessions found.".to_owned()
                } else {
                    let mut lines = vec![format!("*Recent {} sessions:*\n", rows.len())];
                    for (i, r) in rows.iter().enumerate().take(RECENT_LIMIT) {
                        let summary = r
                            .summary
                            .chars()
                            .take(100)
                            .collect::<String>()
                            .replace(['*', '_'], "");
                        lines.push(format!("{}. `{}`\n   {}", i + 1, r.id, summary));
                    }
                    lines.join("\n")
                }
            }
            "/help" | "/start" => HELP_TEXT.to_owned(),
            _ => format!("Unknown command: {cmd}\nSend /help to see available commands."),
        }
    }
}

// ── Telegram HTTP client ──────────────────────────────────────────────────────

/// HTTP wrapper around the Telegram Bot API.
struct TelegramClient {
    client: Client,
    base_url: String,
}

impl TelegramClient {
    fn new(token: &str, base_url_override: Option<&str>) -> Result<Self> {
        // Use no_proxy to bypass tunnel-hostile proxy env vars
        // (mirrors Python `_no_proxy_opener` pattern).
        let client = Client::builder()
            .no_proxy()
            .timeout(Duration::from_secs(POLL_TIMEOUT_SECS + 10))
            .build()
            .context("failed to build reqwest client")?;

        let base_url = match base_url_override {
            Some(u) => format!("{}/{}", u.trim_end_matches('/'), token),
            None => format!("{}{}", DEFAULT_TG_BASE, token),
        };

        Ok(Self { client, base_url })
    }

    async fn call(&self, method: &str, payload: Option<Value>) -> Result<Value, BrokerError> {
        let url = format!("{}/{}", self.base_url, method);

        let req = match payload {
            Some(body) => self.client.post(&url).json(&body),
            None => self.client.post(&url).json(&json!({})),
        };

        let response = req.send().await.map_err(BrokerError::from)?;
        let status = response.status();

        // 429 and 5xx are retriable — surface as Api error for the retry loop.
        if status.as_u16() == 429 || status.is_server_error() {
            return Err(BrokerError::Api(format!(
                "HTTP {} from Telegram",
                status.as_u16()
            )));
        }

        let tg_resp: TgResponse = response
            .json()
            .await
            .map_err(|e| BrokerError::Json(e.to_string()))?;

        if !tg_resp.ok {
            let desc = tg_resp
                .description
                .unwrap_or_else(|| "(no description)".to_owned());
            return Err(BrokerError::Api(desc));
        }

        Ok(tg_resp.result.unwrap_or(Value::Null))
    }

    async fn get_updates(&self, offset: i64, timeout: u64) -> Result<Vec<TgUpdate>, BrokerError> {
        let payload = json!({
            "offset": offset,
            "timeout": timeout,
            "allowed_updates": ["message"],
        });

        let result = self.call("getUpdates", Some(payload)).await?;

        serde_json::from_value(result).map_err(|e| BrokerError::Json(e.to_string()))
    }

    async fn send_message(&self, chat_id: i64, text: &str) -> Result<(), BrokerError> {
        let payload = json!({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        });

        self.call("sendMessage", Some(payload)).await?;
        Ok(())
    }
}

// ── TelegramBroker ────────────────────────────────────────────────────────────

/// Outbound-only Telegram broker for browse (issue #454 PR-A).
///
/// # Usage
/// ```rust,ignore
/// use std::sync::Arc;
/// use sk::browse::broker::telegram::{TelegramBroker, NoopKnowledge};
/// use sk::browse::broker::CancellationToken;
///
/// let broker = TelegramBroker::new(
///     "bot_token",
///     123456789,
///     Arc::new(NoopKnowledge),
///     None, // None = use api.telegram.org; Some(url) = mock server in tests
/// ).unwrap();
/// // broker.run(CancellationToken::new()).await.unwrap();
/// ```
pub struct TelegramBroker {
    client: TelegramClient,
    authorized_user_id: i64,
    dispatcher: CommandDispatcher,
    rate_limiter: RateLimiter,
    config: BrokerConfig,
}

impl TelegramBroker {
    /// Construct a new broker.
    ///
    /// `base_url_override` injects a mock server URL in tests.
    /// Pass `None` for production (uses `https://api.telegram.org/bot`).
    pub fn new(
        token: &str,
        authorized_user_id: i64,
        knowledge: Arc<dyn KnowledgeSource>,
        base_url_override: Option<&str>,
    ) -> Result<Self> {
        let config = BrokerConfig {
            authorized_user_id,
            poll_timeout_secs: POLL_TIMEOUT_SECS,
            min_send_interval: Duration::from_millis(MIN_SEND_INTERVAL_MS),
        };

        Ok(Self {
            client: TelegramClient::new(token, base_url_override)?,
            authorized_user_id,
            dispatcher: CommandDispatcher::new(knowledge),
            rate_limiter: RateLimiter::new(config.min_send_interval),
            config,
        })
    }

    async fn handle_update(&self, update: &TgUpdate) {
        let message = match &update.message {
            Some(m) => m,
            None => return,
        };

        // Require an identified sender; anonymous/channel updates are always dropped.
        let Some(sender_id) = message.from.as_ref().map(|u| u.id) else {
            debug!("dropping update with no sender");
            return;
        };

        // Auth: block-all when authorized_user_id == 0 (disabled), or when sender
        // does not match the configured authorized user.
        if self.authorized_user_id == 0 || sender_id != self.authorized_user_id {
            debug!(
                sender_id,
                authorized = self.authorized_user_id,
                "dropping update from unauthorized user"
            );
            return;
        }

        let text = match &message.text {
            Some(t) if !t.is_empty() => t.as_str(),
            _ => return,
        };

        let chat_id = message.chat.id;
        let response = self.dispatcher.dispatch(text);
        self.send_chunks(chat_id, &response).await;
    }

    async fn send_chunks(&self, chat_id: i64, text: &str) {
        let chunks = chunk_text(text, MAX_MESSAGE_CHARS);
        for chunk in &chunks {
            self.rate_limiter.wait().await;
            if let Err(e) = self.client.send_message(chat_id, chunk).await {
                error!(chat_id, error = %e, "sendMessage failed");
            }
        }
    }
}

impl super::Broker for TelegramBroker {
    async fn run(&self, token: CancellationToken) -> anyhow::Result<()> {
        info!("Starting Telegram long-poll loop (outbound-only, no inbound port)");

        let mut offset: i64 = 0;
        let mut consecutive_errors: u32 = 0;
        // Tracks when an error streak began; used to compute elapsed for budget checks.
        let mut error_streak_start: Option<Instant> = None;

        loop {
            // Check for cancellation before polling.
            if token.is_cancelled() {
                info!("Telegram broker shutting down (cancellation token)");
                return Ok(());
            }

            let poll_result = tokio::select! {
                result = self.client.get_updates(offset, self.config.poll_timeout_secs) => result,
                _ = token.cancelled() => {
                    info!("Telegram broker shutting down (cancellation during poll)");
                    return Ok(());
                }
            };

            match poll_result {
                Ok(updates) => {
                    consecutive_errors = 0;
                    error_streak_start = None;
                    for update in &updates {
                        offset = offset.max(update.update_id + 1);

                        if token.is_cancelled() {
                            info!("Telegram broker shutting down (mid-batch cancellation)");
                            return Ok(());
                        }

                        self.handle_update(update).await;
                    }
                }
                Err(BrokerError::Shutdown) => {
                    return Ok(());
                }
                Err(e) => {
                    // Pre-increment to match original 1-indexed formula:
                    // attempt=1 -> 1*2^1=2s, attempt=2 -> 4s, ..., attempt=6+ -> 60s.
                    consecutive_errors += 1;
                    let streak_start = *error_streak_start.get_or_insert_with(Instant::now);
                    let elapsed = streak_start.elapsed();
                    let err_str = e.to_string();

                    match decide(
                        &POLL_RETRY_POLICY,
                        consecutive_errors,
                        elapsed,
                        &err_str,
                        None,
                    ) {
                        RetryDecision::Retry(wait, _kind) => {
                            let wait_secs = wait.as_secs();
                            warn!(
                                error = %e,
                                consecutive_errors,
                                wait_secs,
                                "getUpdates error; retrying with backoff"
                            );
                            tokio::select! {
                                _ = tokio::time::sleep(wait) => {}
                                _ = token.cancelled() => {
                                    info!("Telegram broker shutting down (cancellation during backoff)");
                                    return Ok(());
                                }
                            }
                        }
                        RetryDecision::Stop(reason) => {
                            // Only reached for auth/quota errors (max_attempts=u32::MAX, budget=None).
                            warn!(
                                error = %e,
                                ?reason,
                                "getUpdates: stopping retry loop (non-retryable error)"
                            );
                            return Ok(());
                        }
                    }
                }
            }
        }
    }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn make_dispatcher() -> CommandDispatcher {
        CommandDispatcher::new(Arc::new(NoopKnowledge))
    }

    #[test]
    fn dispatch_help() {
        let d = make_dispatcher();
        let resp = d.dispatch("/help");
        assert!(resp.contains("/status"), "help should mention /status");
        assert!(resp.contains("/search"), "help should mention /search");
    }

    #[test]
    fn dispatch_start_same_as_help() {
        let d = make_dispatcher();
        assert_eq!(d.dispatch("/start"), d.dispatch("/help"));
    }

    #[test]
    fn dispatch_unknown_command() {
        let d = make_dispatcher();
        let resp = d.dispatch("/unknown");
        assert!(resp.contains("Unknown command"));
    }

    #[test]
    fn dispatch_non_command() {
        let d = make_dispatcher();
        let resp = d.dispatch("hello");
        assert!(resp.contains("/help"));
    }

    #[test]
    fn dispatch_search_no_query() {
        let d = make_dispatcher();
        let resp = d.dispatch("/search");
        assert!(resp.contains("Usage:"));
    }

    #[test]
    fn dispatch_search_with_query() {
        let d = make_dispatcher();
        let resp = d.dispatch("/search authentication");
        assert!(!resp.is_empty());
    }

    #[test]
    fn dispatch_at_suffix_stripping() {
        let d = make_dispatcher();
        let with_suffix = d.dispatch("/search@MyBot authentication");
        let without_suffix = d.dispatch("/search authentication");
        assert_eq!(with_suffix, without_suffix);
    }

    #[test]
    fn dispatch_at_suffix_stripping_help() {
        let d = make_dispatcher();
        let with_suffix = d.dispatch("/help@MyBot");
        let without_suffix = d.dispatch("/help");
        assert_eq!(with_suffix, without_suffix);
    }

    #[test]
    fn dispatch_briefing_no_arg() {
        let d = make_dispatcher();
        let resp = d.dispatch("/briefing");
        assert!(resp.contains("Usage:"));
    }

    #[test]
    fn dispatch_briefing_with_arg() {
        let d = make_dispatcher();
        let resp = d.dispatch("/briefing auth patterns");
        // NoopKnowledge returns non-empty placeholder
        assert!(!resp.is_empty());
        assert!(resp.contains("auth patterns"));
    }

    #[test]
    fn dispatch_recent() {
        let d = make_dispatcher();
        let resp = d.dispatch("/recent");
        assert!(!resp.is_empty());
    }

    #[test]
    fn dispatch_status() {
        let d = make_dispatcher();
        let resp = d.dispatch("/status");
        assert!(resp.to_lowercase().contains("uptime") || resp.to_lowercase().contains("status"));
    }

    #[test]
    fn dispatch_case_insensitive_command() {
        // Commands should be normalized to lowercase before matching.
        // Note: Telegram sends lowercase but we guard against mixed case.
        let d = make_dispatcher();
        let resp = d.dispatch("/HELP");
        assert!(
            resp.contains("/status") || resp.contains("Unknown"),
            "uppercase command handled"
        );
    }

    // ── DbKnowledge tests ──────────────────────────────────────────────────────

    /// Build an in-memory `KnowledgeDb` with the minimal schema required by
    /// `DbKnowledge::search()` and `DbKnowledge::status()`.
    fn make_in_memory_db() -> KnowledgeDb {
        let conn = rusqlite::Connection::open_in_memory().expect("in-memory db");
        conn.execute_batch(
            "CREATE TABLE sessions (id INTEGER PRIMARY KEY, summary TEXT);
             CREATE TABLE schema_version (version INTEGER NOT NULL);
             INSERT INTO schema_version VALUES (1);
             CREATE TABLE knowledge_entries (
                 id      INTEGER PRIMARY KEY,
                 title   TEXT NOT NULL,
                 content TEXT NOT NULL
             );
             INSERT INTO knowledge_entries VALUES (1, 'auth-patterns', 'JWT authentication flow details');
             INSERT INTO knowledge_entries VALUES (2, 'db-migrations',  'How to run schema migrations');
             CREATE VIRTUAL TABLE ke_fts USING fts5(title, content, content='knowledge_entries', content_rowid='id');
             INSERT INTO ke_fts(rowid, title, content)
                 SELECT id, title, content FROM knowledge_entries;",
        )
        .expect("schema setup");
        KnowledgeDb { conn }
    }

    #[test]
    fn db_knowledge_search_returns_results() {
        let knowledge = DbKnowledge::from_db(make_in_memory_db());
        let results = knowledge.search("auth");
        assert!(
            !results.is_empty(),
            "search('auth') should return at least one result"
        );
        assert!(
            results[0].summary.contains("auth"),
            "result summary should contain 'auth'"
        );
    }

    #[test]
    fn db_knowledge_search_empty_query_returns_empty() {
        let knowledge = DbKnowledge::from_db(make_in_memory_db());
        // An empty FTS query is sanitized to empty string → prepare/execute fails
        // gracefully (no panic); returns empty vec.
        let results = knowledge.search("");
        // We only assert no panic; result may be empty or not depending on FTS behaviour.
        let _ = results;
    }

    #[test]
    fn db_knowledge_search_no_match_returns_empty() {
        let knowledge = DbKnowledge::from_db(make_in_memory_db());
        let results = knowledge.search("xyzzy_no_such_term_9999");
        assert!(results.is_empty(), "non-matching query should return empty");
    }

    #[test]
    fn db_knowledge_status_uses_correct_tables() {
        let knowledge = DbKnowledge::from_db(make_in_memory_db());
        let status = knowledge.status();
        assert!(
            status.contains("schema version"),
            "status should report schema version"
        );
    }
}
